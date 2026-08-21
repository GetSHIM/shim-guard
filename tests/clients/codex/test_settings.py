from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

import pytest

from shim_guard.clients.codex.settings import (
    HOOK_TIMEOUT_SECONDS,
    MINIMUM_CODEX_VERSION,
    TESTED_CODEX_VERSION,
    config_path,
    has_inline_hooks,
    hook_command,
    hook_document,
    target_path,
)


def test_codex_0149_settings_are_exact_and_deterministic(tmp_path: Path) -> None:
    interpreter = tmp_path / "venv's python"
    document = hook_document(interpreter)
    assert document == hook_document(interpreter)
    assert document.endswith(b"\n")
    assert json.loads(document) == {
        "hooks": {
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {
                            "command": hook_command(interpreter),
                            "timeout": 30,
                            "type": "command",
                        }
                    ]
                }
            ]
        }
    }
    assert shlex.split(hook_command(interpreter)) == [
        str(interpreter),
        "-I",
        "-B",
        "-m",
        "shim_guard.hook",
    ]
    assert b'"matcher"' not in document
    assert b'"async"' not in document
    assert HOOK_TIMEOUT_SECONDS == 30
    assert TESTED_CODEX_VERSION == MINIMUM_CODEX_VERSION == "0.149.0"


def test_target_path_respects_injected_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ignored"))
    assert target_path(tmp_path) == tmp_path / ".codex" / "hooks.json"


def test_target_path_respects_codex_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "custom-codex"))
    assert target_path() == tmp_path / "custom-codex" / "hooks.json"
    assert config_path() == tmp_path / "custom-codex" / "config.toml"


def test_inline_hook_detection_is_bounded_and_refuses_links(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    assert not has_inline_hooks(config)

    config.write_text('model = "gpt-5"\n')
    assert not has_inline_hooks(config)
    config.write_text("[hooks]\nUserPromptSubmit = []\n")
    assert has_inline_hooks(config)

    config.unlink()
    config.symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError):
        has_inline_hooks(config)

    fifo = tmp_path / "config.fifo"
    os.mkfifo(fifo)
    with pytest.raises(ValueError):
        has_inline_hooks(fifo)


def test_inline_hook_detection_refuses_a_concurrent_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.toml"
    config.write_text('model = "gpt-5"\n')
    replacement = tmp_path / "replacement.toml"
    real_lstat = Path.lstat
    replaced = False

    def replace_before_revalidation(path: Path):
        nonlocal replaced
        if path == config and not replaced:
            replaced = True
            replacement.write_text("[hooks]\n")
            os.replace(replacement, config)
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", replace_before_revalidation)
    with pytest.raises(ValueError, match="changed during inspection"):
        has_inline_hooks(config)


def test_inline_hook_detection_refuses_unsafe_ownership_and_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.toml"
    config.write_text('model = "gpt-5"\n')
    config.chmod(0o620)
    with pytest.raises(ValueError):
        has_inline_hooks(config)

    config.chmod(0o600)
    linked = tmp_path / "linked.toml"
    os.link(config, linked)
    with pytest.raises(ValueError):
        has_inline_hooks(config)
    linked.unlink()

    monkeypatch.setattr(os, "geteuid", lambda: config.stat().st_uid + 1)
    with pytest.raises(ValueError):
        has_inline_hooks(config)


def test_hook_command_requires_absolute_interpreter() -> None:
    with pytest.raises(ValueError):
        hook_command("python3")
    with pytest.raises(ValueError):
        hook_command("/tmp/unsafe\x1b-python")
