"""Codex 0.149.0 user hook settings owned by SHIM Guard."""

from __future__ import annotations

import json
import os
import shlex
import stat
import sys
import tomllib
from pathlib import Path

TESTED_CODEX_VERSION = "0.149.0"
MINIMUM_CODEX_VERSION = "0.149.0"
HOOK_TIMEOUT_SECONDS = 30
MAX_CONFIG_BYTES = 1_000_000
_UNSAFE_WRITABLE = stat.S_IWGRP | stat.S_IWOTH


def _codex_home(home: Path | None = None) -> Path:
    if home is not None:
        return Path(home) / ".codex"
    if configured := os.environ.get("CODEX_HOME"):
        return Path(configured).expanduser()
    return Path.home() / ".codex"


def target_path(home: Path | None = None) -> Path:
    """Return the user-scoped Codex hook document path."""
    return _codex_home(home) / "hooks.json"


def config_path(home: Path | None = None) -> Path:
    """Return the same-layer Codex TOML configuration path."""
    return _codex_home(home) / "config.toml"


def inspect_inline_hooks(
    path: Path | None = None,
) -> tuple[bool, os.stat_result | None]:
    """Detect hooks and retain the exact inspected state for publication."""
    target = config_path() if path is None else Path(path)
    if (
        not target.is_absolute()
        or ".." in target.parts
        or not str(target).isprintable()
    ):
        raise ValueError("Codex config path cannot be inspected safely")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(target, flags)
    except FileNotFoundError:
        return False, None
    except OSError as error:
        raise ValueError("Codex config cannot be inspected safely") from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or opened.st_mode & _UNSAFE_WRITABLE
            or opened.st_size > MAX_CONFIG_BYTES
        ):
            raise ValueError("Codex config cannot be inspected safely")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            data = stream.read(MAX_CONFIG_BYTES + 1)
            closed = os.fstat(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(data) > MAX_CONFIG_BYTES:
        raise ValueError("Codex config cannot be inspected safely")
    try:
        latest = target.lstat()
    except OSError as error:
        raise ValueError("Codex config changed during inspection") from error
    opened_fingerprint = (
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_ctime_ns,
    )
    closed_fingerprint = (
        closed.st_dev,
        closed.st_ino,
        closed.st_size,
        closed.st_mtime_ns,
        closed.st_ctime_ns,
    )
    latest_fingerprint = (
        latest.st_dev,
        latest.st_ino,
        latest.st_size,
        latest.st_mtime_ns,
        latest.st_ctime_ns,
    )
    if (
        not stat.S_ISREG(latest.st_mode)
        or opened_fingerprint != closed_fingerprint
        or closed_fingerprint != latest_fingerprint
    ):
        raise ValueError("Codex config changed during inspection")
    try:
        config = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError("Codex config cannot be inspected safely") from error
    return "hooks" in config, latest


def has_inline_hooks(path: Path | None = None) -> bool:
    """Detect hooks in Codex TOML without following links or exposing content."""
    return inspect_inline_hooks(path)[0]


def hook_command(interpreter: str | Path = sys.executable) -> str:
    """Return the isolated hook command with an absolute interpreter."""
    executable = Path(interpreter)
    if not executable.is_absolute() or not str(executable).isprintable():
        raise ValueError("hook interpreter must be an absolute safe path")
    return shlex.join((str(executable), "-I", "-B", "-m", "shim_guard.hook"))


def hook_document(interpreter: str | Path = sys.executable) -> bytes:
    """Return the exact deterministic UTF-8 document SHIM exclusively owns."""
    document = {
        "hooks": {
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {
                            "command": hook_command(interpreter),
                            "timeout": HOOK_TIMEOUT_SECONDS,
                            "type": "command",
                        }
                    ]
                }
            ]
        }
    }
    return (
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
