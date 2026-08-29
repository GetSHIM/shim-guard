"""Codex 0.149.0 user hook settings owned by SHIM Guard."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

try:  # pragma: no cover - exercised by whichever interpreter runs the tests
    import tomllib  # ty: ignore[unresolved-import]
except ModuleNotFoundError:  # Python < 3.11; CLI-only, never on the hook path
    import tomli as tomllib

from shim_guard.clients.hook_settings import (
    MAX_SETTINGS_BYTES,
    Registration,
    add_groups,
    remove_groups,
)
from shim_guard.events.registry import INSTALLED
from shim_guard.installation import StateKind, inspect_file

TESTED_CODEX_VERSION = "0.149.0"
MINIMUM_CODEX_VERSION = "0.149.0"
HOOK_TIMEOUT_SECONDS = 30
MAX_CONFIG_BYTES = MAX_SETTINGS_BYTES
PROMPT_EVENT = "UserPromptSubmit"


def _codex_home(home: Path | None = None) -> Path:
    try:
        if home is not None:
            return Path(home) / ".codex"
        if configured := os.environ.get("CODEX_HOME"):
            return Path(configured).expanduser()
        return Path.home() / ".codex"
    except RuntimeError as error:
        raise ValueError("Codex home path is invalid") from error


def target_path(home: Path | None = None) -> Path:
    """Return the user-scoped Codex hook document path."""
    return _codex_home(home) / "hooks.json"


def config_path(home: Path | None = None) -> Path:
    """Return the same-layer Codex TOML configuration path."""
    return _codex_home(home) / "config.toml"


def has_inline_hooks(path: Path | None = None) -> bool:
    """Detect hooks in Codex TOML without following links or exposing content."""
    target = config_path() if path is None else Path(path)
    state = inspect_file(target, MAX_CONFIG_BYTES)
    if state.kind is StateKind.ABSENT:
        return False
    if state.kind is not StateKind.FILE:
        raise ValueError("Codex config cannot be inspected safely")
    assert state.content is not None
    try:
        config = tomllib.loads(state.content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError) as error:
        raise ValueError("Codex config cannot be inspected safely") from error
    return "hooks" in config


def hook_command(interpreter: str | Path = sys.executable) -> str:
    """Return the isolated hook command with an absolute interpreter."""
    executable = Path(interpreter)
    if not executable.is_absolute() or not str(executable).isprintable():
        raise ValueError("hook interpreter must be an absolute safe path")
    return shlex.join((str(executable), "-I", "-B", "-m", "shim_guard.hook"))


def hook_group(interpreter: str | Path = sys.executable) -> dict[str, object]:
    """Return SHIM's exact Codex matcher group."""
    return {
        "hooks": [
            {
                "command": hook_command(interpreter),
                "timeout": HOOK_TIMEOUT_SECONDS,
                "type": "command",
            }
        ]
    }


def hook_groups(interpreter: str | Path = sys.executable) -> tuple[Registration, ...]:
    """Return every event SHIM registers, prompt first then tool events.

    Codex's tool adapters are report-only until a probe confirms their mutation
    shape, so today this is the prompt event alone. Promoting them in the
    registry installs them here with no further edit.
    """
    groups: list[Registration] = [(PROMPT_EVENT, hook_group(interpreter))]
    groups.extend(
        (event, hook_group(interpreter))
        for client, event in INSTALLED
        if client == "codex"
    )
    return tuple(groups)


def add_hook(content: bytes | None, interpreter: str | Path = sys.executable) -> bytes:
    """Append SHIM's groups while preserving existing hook ordering."""
    return add_groups(content, hook_groups(interpreter))


def remove_hook(content: bytes, interpreter: str | Path = sys.executable) -> bytes:
    """Remove exactly SHIM's groups and preserve every unrelated entry."""
    return remove_groups(content, hook_groups(interpreter))
