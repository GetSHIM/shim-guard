"""Codex 0.149.0 user hook settings owned by SHIM Guard."""

from __future__ import annotations

import json
import math
import os
import shlex
import stat
import sys
import tomllib
from pathlib import Path
from typing import cast

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


def has_inline_hooks(path: Path | None = None) -> bool:
    """Detect hooks in Codex TOML without following links or exposing content."""
    target = config_path() if path is None else Path(path)
    if (
        not target.is_absolute()
        or ".." in target.parts
        or not str(target).isprintable()
    ):
        raise ValueError("Codex config path cannot be inspected safely")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = -1
    try:
        descriptor = os.open(target, flags)
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
    except FileNotFoundError:
        return False
    except OSError as error:
        raise ValueError("Codex config cannot be inspected safely") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(data) > MAX_CONFIG_BYTES:
        raise ValueError("Codex config cannot be inspected safely")
    if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
        closed.st_size,
        closed.st_mtime_ns,
        closed.st_ctime_ns,
    ):
        raise ValueError("Codex config changed during inspection")
    try:
        config = tomllib.loads(data.decode("utf-8"))
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


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate JSON key")
    return value


def _constant(_: str) -> None:
    raise ValueError("non-finite JSON number")


def _float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def _load(content: bytes) -> dict[str, object]:
    if len(content) > MAX_CONFIG_BYTES:
        raise ValueError("Codex hook document is too large")
    try:
        document = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_object,
            parse_constant=_constant,
            parse_float=_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("invalid Codex hook document") from error
    if not isinstance(document, dict):
        raise ValueError("Codex hook document must be an object")
    if "hooks" not in document:
        return document
    hooks = document["hooks"]
    if not isinstance(hooks, dict):
        raise ValueError("Codex hooks must be an object")
    for groups in hooks.values():
        if not isinstance(groups, list):
            raise ValueError("Codex hook events must be lists")
        for group in groups:
            if not isinstance(group, dict):
                raise ValueError("Codex matcher groups must be objects")
            if "hooks" not in group:
                raise ValueError("Codex matcher groups require hooks")
            handlers = group["hooks"]
            if not isinstance(handlers, list):
                raise ValueError("Codex matcher hooks must be lists")
            for handler in handlers:
                if not isinstance(handler, dict):
                    raise ValueError("Codex hook handlers must be objects")
                hook_type = handler.get("type")
                if not isinstance(hook_type, str):
                    raise ValueError("Codex hook handler type must be a string")
                if hook_type == "command" and not (
                    isinstance(handler.get("command"), str) and handler["command"]
                ):
                    raise ValueError("Codex command hooks require a command")
    return document


def _dump(document: dict[str, object]) -> bytes:
    try:
        content = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()
    except (UnicodeEncodeError, RecursionError) as error:
        raise ValueError("invalid Unicode in Codex hook document") from error
    if len(content) > MAX_CONFIG_BYTES:
        raise ValueError("Codex hook document is too large")
    return content


def _same_group(left: object, right: object) -> bool:
    def canonical(value: object) -> str:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    return canonical(left) == canonical(right)


def add_hook(content: bytes | None, interpreter: str | Path = sys.executable) -> bytes:
    """Append SHIM's group while preserving existing hook ordering."""
    document = {} if content is None else _load(content)
    hooks = cast(dict[str, object], document.setdefault("hooks", {}))
    groups = cast(list[object], hooks.setdefault("UserPromptSubmit", []))
    group = hook_group(interpreter)
    matches = [item for item in groups if _same_group(item, group)]
    if len(matches) > 1:
        raise ValueError("duplicate SHIM hook groups are ambiguous")
    if matches:
        assert content is not None
        return content
    groups.append(group)
    return _dump(document)


def remove_hook(content: bytes, interpreter: str | Path = sys.executable) -> bytes:
    """Remove exactly one SHIM group and preserve every unrelated entry."""
    document = _load(content)
    if "hooks" not in document:
        return content
    hooks = cast(dict[str, object], document["hooks"])
    groups = cast(list[object], hooks.get("UserPromptSubmit", []))
    group = hook_group(interpreter)
    matches = [index for index, item in enumerate(groups) if _same_group(item, group)]
    if len(matches) > 1:
        raise ValueError("duplicate SHIM hook groups are ambiguous")
    if not matches:
        return content
    del groups[matches[0]]
    if not groups:
        del hooks["UserPromptSubmit"]
    if not hooks:
        del document["hooks"]
    return _dump(document)
