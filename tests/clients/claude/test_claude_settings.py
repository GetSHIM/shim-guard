from __future__ import annotations

import json
from pathlib import Path

import pytest

from shim_guard.clients.claude.settings import (
    HOOK_TIMEOUT_SECONDS,
    MINIMUM_CLAUDE_VERSION,
    TESTED_CLAUDE_VERSION,
    add_hook,
    hook_group,
    remove_hook,
    target_path,
)


def test_claude_code_settings_use_shell_free_exec_form(tmp_path: Path) -> None:
    interpreter = tmp_path / "venv's python"
    group = hook_group(interpreter)

    assert group == {
        "hooks": [
            {
                "args": ["-I", "-B", "-m", "shim_guard.hook", "claude"],
                "command": str(interpreter),
                "timeout": 30,
                "type": "command",
            }
        ]
    }
    assert json.loads(add_hook(None, interpreter)) == {
        "hooks": {"UserPromptSubmit": [group]}
    }
    assert HOOK_TIMEOUT_SECONDS == 30
    assert TESTED_CLAUDE_VERSION == MINIMUM_CLAUDE_VERSION == "2.1.210"


def test_claude_code_target_respects_config_dir_and_injected_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "custom-claude"))
    assert target_path() == tmp_path / "custom-claude" / "settings.json"
    assert target_path(tmp_path) == tmp_path / ".claude" / "settings.json"


def test_claude_code_settings_preserve_unrelated_values_and_hooks(
    tmp_path: Path,
) -> None:
    interpreter = tmp_path / "python"
    existing_group = {"hooks": [{"type": "command", "command": "existing"}]}
    original = {
        "permissions": {"allow": ["Read"]},
        "hooks": {
            "SessionStart": [existing_group],
            "UserPromptSubmit": [existing_group],
        },
        "model": "sonnet",
    }

    installed = add_hook(json.dumps(original).encode(), interpreter)
    document = json.loads(installed)
    assert list(document) == ["permissions", "hooks", "model"]
    assert document["permissions"] == original["permissions"]
    assert document["hooks"]["SessionStart"] == [existing_group]
    assert document["hooks"]["UserPromptSubmit"] == [
        existing_group,
        hook_group(interpreter),
    ]
    assert add_hook(installed, interpreter) == installed
    assert json.loads(remove_hook(installed, interpreter)) == original


@pytest.mark.parametrize(
    "content",
    [
        b'{"hooks":{} ,"hooks":{}}',
        b'{"value":NaN}',
        b"[]",
        b'{"hooks":null}',
        b'{"hooks":{"UserPromptSubmit":{}}}',
        b'{"hooks":{"UserPromptSubmit":[{}]}}',
        b'{"hooks":{"UserPromptSubmit":[{"hooks":[{}]}]}}',
    ],
)
def test_claude_code_settings_reject_malformed_documents(content: bytes) -> None:
    with pytest.raises(ValueError):
        add_hook(content)
