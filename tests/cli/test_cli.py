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


def _guard_config(monkeypatch, tmp_path: Path) -> Path:
    target = tmp_path / "settings" / "config.toml"
    monkeypatch.setenv("SHIM_GUARD_CONFIG", str(target))
    return target


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


def test_help_command_lists_a_description_for_every_command() -> None:
    result = runner.invoke(app, ["help"], color=False)
    descriptions = (
        "Show command usage and descriptions.",
        "Run the local synthetic detector proof.",
        "Scan bounded UTF-8 text from standard input.",
        "Redact bounded UTF-8 text from standard input.",
        "Show or change locally enabled sensitive-data entities.",
        "Preview or install a client prompt hook.",
        "Show the prompt-hook installation state.",
        "Run client compatibility and hook health checks.",
        "Remove only SHIM Guard's client prompt hook.",
    )

    assert result.exit_code == 0
    assert "Usage: shim [OPTIONS] COMMAND [ARGS]..." in result.output
    assert all(description in result.output for description in descriptions)


def test_client_arguments_list_and_enforce_available_value() -> None:
    for command in ("demo", "install", "doctor", "revert"):
        help_result = runner.invoke(app, [command, "--help"], color=False)
        invalid_result = runner.invoke(app, [command, "other"], color=False)

        assert help_result.exit_code == 0
        assert "codex" in help_result.output
        assert invalid_result.exit_code == 2
        assert "'codex'" in invalid_result.output


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


def test_config_selects_entities_for_privacy_commands(
    monkeypatch, tmp_path: Path
) -> None:
    target = _guard_config(monkeypatch, tmp_path)

    initial = runner.invoke(app, ["config"], color=False)
    path_scan = runner.invoke(
        app,
        ["scan", "--json"],
        input="Read /Users/alice/.ssh/id_rsa, then continue",
    )
    saved = runner.invoke(
        app,
        ["config", "--only", "EMAIL", "--only", "SECRET", "--yes"],
        color=False,
    )
    scan = runner.invoke(
        app,
        ["scan", "--json"],
        input="alice@example.com +90 532 123 45 67",
    )
    current = runner.invoke(app, ["config", "--json"])
    narrow = runner.invoke(app, ["config"], env={"COLUMNS": "20"}, color=False)
    adjusted = runner.invoke(
        app,
        ["config", "--enable", "phone", "--disable", "secret", "--yes"],
    )
    final = runner.invoke(app, ["config", "--json"])

    assert path_scan.exit_code == 0
    assert json.loads(path_scan.output)["status"] == "safe"
    assert initial.exit_code == saved.exit_code == current.exit_code == 0
    assert adjusted.exit_code == final.exit_code == 0
    assert "Current detection: 11/11 enabled" in initial.output
    assert "ON" in saved.output and "OFF" in saved.output
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(scan.output)["counts"] == {"EMAIL": 1}
    payload = json.loads(current.output)
    assert payload["enabled_entities"] == ["EMAIL", "SECRET"]
    assert "PHONE" in payload["disabled_entities"]
    assert "OFF TR_NATIONAL_ID" in narrow.output
    assert max(map(len, narrow.output.splitlines())) <= 20
    assert json.loads(final.output)["enabled_entities"] == ["EMAIL", "PHONE"]


def test_config_recovers_invalid_settings_and_rejects_conflicts(
    monkeypatch, tmp_path: Path
) -> None:
    target = _guard_config(monkeypatch, tmp_path)
    target.parent.mkdir()
    target.write_bytes(b"not toml")

    blocked = runner.invoke(app, ["scan"], input="plain text")
    conflict = runner.invoke(
        app, ["config", "--enable", "EMAIL", "--disable", "EMAIL", "--yes"]
    )
    reset = runner.invoke(app, ["config", "--reset", "--yes"])

    assert blocked.exit_code == 1
    assert "Unable to process stdin" in blocked.output
    assert conflict.exit_code == 2
    assert "cannot be enabled and disabled" in conflict.output
    assert reset.exit_code == 0
    assert "EMAIL" in target.read_text()


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
    target = home / ".codex" / "hooks.json"

    preview = runner.invoke(app, ["install", "codex", "--dry-run"])
    assert preview.exit_code == 0
    assert not target.exists()

    installed = runner.invoke(app, ["install", "codex", "--yes"])
    current = runner.invoke(app, ["status", "--json"])
    reverted = runner.invoke(app, ["revert", "codex", "--yes"])

    assert installed.exit_code == 0
    assert json.loads(current.output)["state"] == "installed"
    assert reverted.exit_code == 0
    assert target.read_bytes() == b"{}\n"


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
        "entity_settings",
        "runner",
        "hook_activation",
    }
    assert payload["status"] == "warning"


def test_install_preserves_shared_hooks_and_preview_hides_them(
    monkeypatch, tmp_path: Path
) -> None:
    from shim_guard.clients.codex.settings import hook_group

    home = _codex_home(monkeypatch, tmp_path)
    target = home / ".codex" / "hooks.json"
    existing_group = {"hooks": [{"type": "command", "command": "existing-secret-hook"}]}
    original = {
        "version": 1,
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command", "command": "session-hook"}]}
            ],
            "UserPromptSubmit": [existing_group],
        },
    }
    target.write_text(json.dumps(original))

    preview = runner.invoke(app, ["install", "codex", "--dry-run"])
    installed = runner.invoke(app, ["install", "codex", "--yes"])
    first_install = target.read_bytes()
    repeated = runner.invoke(app, ["install", "codex", "--yes"])
    reverted = runner.invoke(app, ["revert", "codex", "--yes"])

    assert preview.exit_code == installed.exit_code == repeated.exit_code == 0
    assert "existing-secret-hook" not in preview.output
    assert "will be preserved" in installed.output
    assert "appended last" in installed.output
    installed_document = json.loads(first_install)
    assert installed_document["version"] == 1
    assert (
        installed_document["hooks"]["SessionStart"] == original["hooks"]["SessionStart"]
    )
    assert installed_document["hooks"]["UserPromptSubmit"] == [
        existing_group,
        hook_group(),
    ]
    assert json.loads(target.read_bytes()) == original
    assert reverted.exit_code == 0


def test_repeated_install_does_not_reformat_existing_document(
    monkeypatch, tmp_path: Path
) -> None:
    from shim_guard.clients.codex.settings import hook_group

    home = _codex_home(monkeypatch, tmp_path)
    target = home / ".codex" / "hooks.json"
    content = json.dumps(
        {"hooks": {"UserPromptSubmit": [hook_group()]}}, separators=(",", ":")
    ).encode()
    target.write_bytes(content)

    result = runner.invoke(app, ["install", "codex", "--yes"])

    assert result.exit_code == 0
    assert target.read_bytes() == content


def test_install_leaves_inline_hooks_untouched(monkeypatch, tmp_path: Path) -> None:
    home = _codex_home(monkeypatch, tmp_path)
    config = home / ".codex" / "config.toml"
    inline = (
        "[hooks]\n"
        'UserPromptSubmit = [{ hooks = [{ type = "command", command = "inline" }] }]\n'
    )
    config.write_text(inline)

    result = runner.invoke(app, ["install", "codex", "--yes"])

    assert result.exit_code == 0
    assert "stay untouched" in result.output
    assert config.read_text() == inline
    assert (home / ".codex" / "hooks.json").exists()


def test_install_refuses_hook_document_changed_during_confirmation(
    monkeypatch, tmp_path: Path
) -> None:
    home = _codex_home(monkeypatch, tmp_path)
    target = home / ".codex" / "hooks.json"
    target.write_text('{"hooks":{"UserPromptSubmit":[]}}')
    changed = {
        "hooks": {
            "UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": "concurrent-hook"}]}
            ]
        }
    }

    def change_hooks(*_args, **_kwargs) -> bool:
        target.write_text(json.dumps(changed))
        return True

    monkeypatch.setattr("shim_guard.cli.integrations.typer.confirm", change_hooks)
    result = runner.invoke(app, ["install", "codex"])

    assert result.exit_code == 2
    assert json.loads(target.read_bytes()) == changed


def test_install_refuses_malformed_hook_document(monkeypatch, tmp_path: Path) -> None:
    home = _codex_home(monkeypatch, tmp_path)
    target = home / ".codex" / "hooks.json"
    target.write_bytes(b'{"hooks":')

    result = runner.invoke(app, ["install", "codex", "--yes"])

    assert result.exit_code == 2
    assert target.read_bytes() == b'{"hooks":'


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
