"""Claude Code 2.1.210 user hook settings owned by SHIM Guard."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from shim_guard.clients.hook_settings import (
    MAX_SETTINGS_BYTES,
    Registration,
    add_groups,
    remove_groups,
)
from shim_guard.events.registry import INSTALLED
from shim_guard.session import SESSION_EVENTS

#: The newest release the hooks were driven against end to end. Tool events
#: were verified here; the prompt event has worked since the minimum.
TESTED_CLAUDE_VERSION = "2.1.251"
MINIMUM_CLAUDE_VERSION = "2.1.210"
HOOK_TIMEOUT_SECONDS = 30
MAX_CONFIG_BYTES = MAX_SETTINGS_BYTES
PROMPT_EVENT = "UserPromptSubmit"
#: Tool events are per-tool by design; SHIM classifies by direction itself and
#: needs to see every tool, so it registers the match-everything matcher.
TOOL_MATCHER = "*"


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


def _handler(interpreter: str | Path) -> dict[str, object]:
    executable = Path(interpreter)
    if not executable.is_absolute() or not str(executable).isprintable():
        raise ValueError("hook interpreter must be an absolute safe path")
    return {
        "args": ["-I", "-B", "-m", "shim_guard.hook", "claude"],
        "command": str(executable),
        "timeout": HOOK_TIMEOUT_SECONDS,
        "type": "command",
    }


def hook_group(interpreter: str | Path = sys.executable) -> dict[str, object]:
    """Return SHIM's exact prompt hook group in shell-free exec form."""
    return {"hooks": [_handler(interpreter)]}


def tool_hook_group(interpreter: str | Path = sys.executable) -> dict[str, object]:
    """Return SHIM's exact tool hook group, which matches every tool."""
    return {"matcher": TOOL_MATCHER, "hooks": [_handler(interpreter)]}


def hook_groups(interpreter: str | Path = sys.executable) -> tuple[Registration, ...]:
    """Return every event SHIM registers: prompt, tool, then session.

    The tool events come from the adapter registry rather than a list here, so
    an adapter that is promoted to verified is installed by the next `shim
    install` without a second edit in this module. The session events carry no
    matcher because they are not per-tool.
    """
    groups: list[Registration] = [(PROMPT_EVENT, hook_group(interpreter))]
    groups.extend(
        (event, tool_hook_group(interpreter))
        for client, event in INSTALLED
        if client == "claude"
    )
    groups.extend((event, hook_group(interpreter)) for event in SESSION_EVENTS)
    return tuple(groups)


def add_hook(content: bytes | None, interpreter: str | Path = sys.executable) -> bytes:
    """Append SHIM's groups while preserving existing Claude Code settings."""
    return add_groups(content, hook_groups(interpreter))


def remove_hook(content: bytes, interpreter: str | Path = sys.executable) -> bytes:
    """Remove exactly SHIM's groups and preserve every unrelated setting."""
    return remove_groups(content, hook_groups(interpreter))
