"""Claude Code 2.1.210 user hook settings owned by SHIM Guard."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from shim_guard.clients.user_prompt_settings import (
    MAX_SETTINGS_BYTES,
    add_group,
    remove_group,
)

TESTED_CLAUDE_VERSION = "2.1.210"
MINIMUM_CLAUDE_VERSION = "2.1.210"
HOOK_TIMEOUT_SECONDS = 30
MAX_CONFIG_BYTES = MAX_SETTINGS_BYTES


def _claude_home(home: Path | None = None) -> Path:
    try:
        if home is not None:
            return Path(home) / ".claude"
        if configured := os.environ.get("CLAUDE_CONFIG_DIR"):
            return Path(configured).expanduser()
        return Path.home() / ".claude"
    except RuntimeError as error:
        raise ValueError("Claude Code home path is invalid") from error


def target_path(home: Path | None = None) -> Path:
    """Return the user-scoped Claude Code settings path."""
    return _claude_home(home) / "settings.json"


def hook_group(interpreter: str | Path = sys.executable) -> dict[str, object]:
    """Return SHIM's exact Claude Code hook group in shell-free exec form."""
    executable = Path(interpreter)
    if not executable.is_absolute() or not str(executable).isprintable():
        raise ValueError("hook interpreter must be an absolute safe path")
    return {
        "hooks": [
            {
                "args": ["-I", "-B", "-m", "shim_guard.hook", "claude"],
                "command": str(executable),
                "timeout": HOOK_TIMEOUT_SECONDS,
                "type": "command",
            }
        ]
    }


def add_hook(content: bytes | None, interpreter: str | Path = sys.executable) -> bytes:
    """Append SHIM's group while preserving existing Claude Code settings."""
    return add_group(content, hook_group(interpreter))


def remove_hook(content: bytes, interpreter: str | Path = sys.executable) -> bytes:
    """Remove exactly one SHIM group and preserve every unrelated setting."""
    return remove_group(content, hook_group(interpreter))
