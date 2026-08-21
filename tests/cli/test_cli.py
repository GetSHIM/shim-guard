from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from shim_guard.cli import output
from shim_guard.cli.app import app
from shim_guard.cli.output import terminal_text
from shim_guard.clients import CLIENT_NAMES

runner = CliRunner()


def _codex_home(monkeypatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


def _codex(monkeypatch, tmp_path: Path, version: str = "0.149.0") -> None:
    executable = tmp_path / "codex"
    executable.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = features ]; then\n'
        "  printf 'hooks stable true\\n'\n"
        "else\n"
        f"  printf 'codex {version}\\n'\n"
        "fi\n"
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")


def test_help_does_not_load_detector() -> None:
    script = (
        "import sys; from typer.testing import CliRunner; "
        "from shim_guard.cli.app import app; "
        "result = CliRunner().invoke(app, ['--help']); "
        "raise SystemExit(0 if result.exit_code == 0 and 'presidio_analyzer' not in sys.modules else 1)"
    )
    result = subprocess.run([sys.executable, "-I", "-c", script], check=False)

    assert result.returncode == 0


def test_help_remains_readable_at_narrow_terminal_width() -> None:
    result = runner.invoke(app, ["--help"], env={"COLUMNS": "20"}, color=False)

    assert result.exit_code == 0
    assert "Usage:" in result.output
    assert "Commands" in result.output
    assert max(map(len, result.output.splitlines())) <= 20


def test_client_arguments_list_and_enforce_available_value() -> None:
    assert CLIENT_NAMES
    for command in ("demo", "install", "doctor", "revert"):
        help_result = runner.invoke(app, [command, "--help"], color=False)
        invalid_result = runner.invoke(app, [command, "other"], color=False)

        assert help_result.exit_code == 0
        assert all(name in help_result.output for name in CLIENT_NAMES)
        assert invalid_result.exit_code == 2
        assert all(repr(name) in invalid_result.output for name in CLIENT_NAMES)


def test_privacy_stdin_json_and_no_color(monkeypatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")

    safe = runner.invoke(app, ["scan"], input="plain synthetic text")
    finding = runner.invoke(app, ["scan", "--json"], input="email alice@example.com")
    redacted = runner.invoke(app, ["redact"], input="email alice@example.com")
    redacted_json = runner.invoke(
        app, ["redact", "--json"], input="Contact alice@example.invalid"
    )
    demo = runner.invoke(app, ["demo", "codex", "--json"])
    invalid = runner.invoke(app, ["scan"], input=b"\xff")

    assert safe.exit_code == 0
    assert "\x1b" not in safe.output
    assert finding.exit_code == 1
    assert "alice@example.com" not in finding.output
    assert json.loads(finding.output)["schema_version"] == 1
    assert redacted.exit_code == 0
    assert redacted.output == "email <EMAIL_1>\n"
    assert "alice@example.invalid" not in redacted_json.output
    assert "redacted_text" not in json.loads(redacted_json.output)
    assert "redacted_text" not in json.loads(demo.output)
    assert invalid.exit_code == 1
    assert "Unable to process stdin" in invalid.output


def test_empty_no_color_value_disables_ansi(monkeypatch) -> None:
    stream = io.StringIO()
    monkeypatch.setattr(stream, "isatty", lambda: True)
    monkeypatch.setattr(output.sys, "stdout", stream)
    monkeypatch.setenv("NO_COLOR", "")

    output.emit("PASS", "Readable without color.")

    assert "\x1b" not in stream.getvalue()


def test_redaction_escapes_terminal_controls_only_for_a_tty(monkeypatch) -> None:
    monkeypatch.setattr(output.sys.stdout, "isatty", lambda: True)
    assert terminal_text("safe\x1b[31m", output.sys.stdout) == r"safe\x1b[31m"

    monkeypatch.setattr(output.sys.stdout, "isatty", lambda: False)
    assert terminal_text("safe\x1b[31m", output.sys.stdout) == "safe\x1b[31m"


def test_install_status_and_revert(monkeypatch, tmp_path: Path) -> None:
    home = _codex_home(monkeypatch, tmp_path)

    preview = runner.invoke(app, ["install", "codex", "--dry-run"])
    installed = runner.invoke(app, ["install", "codex", "--yes"])
    current = runner.invoke(app, ["status", "--json"])
    reverted = runner.invoke(app, ["revert", "codex", "--yes"])

    assert preview.exit_code == 0
    assert not (home / ".codex" / "hooks.json").exists()
    assert installed.exit_code == 0
    assert json.loads(current.output)["state"] == "installed"
    assert reverted.exit_code == 0
    assert not (home / ".codex" / "hooks.json").exists()


def test_confirmation_and_doctor(monkeypatch, tmp_path: Path) -> None:
    _codex_home(monkeypatch, tmp_path)
    _codex(monkeypatch, tmp_path)

    cancelled = runner.invoke(app, ["install", "codex"], input="n\n")
    doctor = runner.invoke(app, ["doctor", "codex", "--json"])

    assert cancelled.exit_code == 1
    assert "cancelled" in cancelled.output.lower()
    payload = json.loads(doctor.output)
    assert payload["schema_version"] == 1
    assert {item["name"] for item in payload["checks"]} == {
        "codex",
        "hooks_feature",
        "hook_configuration",
        "runner",
        "hook_activation",
    }
    assert payload["status"] == "warning"


def test_install_refuses_same_layer_inline_hooks(monkeypatch, tmp_path: Path) -> None:
    home = _codex_home(monkeypatch, tmp_path)
    (home / ".codex" / "config.toml").write_text("[hooks]\nUserPromptSubmit = []\n")

    result = runner.invoke(app, ["install", "codex", "--yes"])

    assert result.exit_code == 2
    assert not (home / ".codex" / "hooks.json").exists()


def test_install_accepts_unchanged_config_without_hooks(
    monkeypatch, tmp_path: Path
) -> None:
    home = _codex_home(monkeypatch, tmp_path)
    (home / ".codex" / "config.toml").write_text('model = "gpt-5"\n')

    result = runner.invoke(app, ["install", "codex", "--yes"])

    assert result.exit_code == 0
    assert (home / ".codex" / "hooks.json").exists()


def test_install_refuses_inline_hooks_added_during_confirmation(
    monkeypatch, tmp_path: Path
) -> None:
    home = _codex_home(monkeypatch, tmp_path)

    def add_inline_hooks(*_args, **_kwargs) -> bool:
        (home / ".codex" / "config.toml").write_text("[hooks]\n")
        return True

    monkeypatch.setattr("shim_guard.cli.integrations.typer.confirm", add_inline_hooks)
    result = runner.invoke(app, ["install", "codex"])

    assert result.exit_code == 2
    assert not (home / ".codex" / "hooks.json").exists()


def test_install_refuses_inline_hooks_added_at_publication(
    monkeypatch, tmp_path: Path
) -> None:
    from shim_guard.installation import files

    home = _codex_home(monkeypatch, tmp_path)
    (home / ".codex" / "config.toml").write_text('model = "gpt-5"\n')
    real_apply = files.apply

    def add_inline_hooks_after_final_plan(plan) -> bool:
        (home / ".codex" / "config.toml").write_text("[hooks]\n")
        return real_apply(plan)

    monkeypatch.setattr(files, "apply", add_inline_hooks_after_final_plan)
    result = runner.invoke(app, ["install", "codex", "--yes"])

    assert result.exit_code == 2
    assert not (home / ".codex" / "hooks.json").exists()


def test_doctor_version_states(monkeypatch, tmp_path: Path) -> None:
    _codex_home(monkeypatch, tmp_path)

    monkeypatch.setenv("PATH", "")
    missing = runner.invoke(app, ["doctor", "codex", "--json"])
    _codex(monkeypatch, tmp_path, "0.148.0")
    older = runner.invoke(app, ["doctor", "codex", "--json"])
    _codex(monkeypatch, tmp_path, "0.150.0")
    future = runner.invoke(app, ["doctor", "codex", "--json"])
    _codex(monkeypatch, tmp_path, "0.149.0")
    current = runner.invoke(app, ["doctor", "codex", "--json"])

    def codex_status(result) -> str:
        return json.loads(result.output)["checks"][0]["status"]

    assert codex_status(missing) == "FAIL"
    assert codex_status(older) == "FAIL"
    assert codex_status(future) == "WARN"
    assert codex_status(current) == "PASS"
