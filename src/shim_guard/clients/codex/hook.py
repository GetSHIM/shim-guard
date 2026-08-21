"""Codex ``UserPromptSubmit`` input and output codec."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shim_guard.guard import GuardDecision

EVENT_NAME = "UserPromptSubmit"
MAX_REASON_CHARS = 4_000
MAX_OUTPUT_BYTES = 4_096
_ERROR_REASON = (
    "SHIM Guard could not safely inspect this prompt. "
    "Try again or run `shim scan` locally."
)


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def parse_input(raw: bytes) -> str:
    """Return the submitted prompt from one strict Codex hook payload."""
    try:
        payload = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_object,
            parse_constant=_reject_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ) as error:
        raise ValueError("invalid Codex hook payload") from error
    if not isinstance(payload, dict):
        raise ValueError("Codex hook payload must be an object")
    if payload.get("hook_event_name") != EVENT_NAME:
        raise ValueError("unexpected Codex hook event")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        raise ValueError("Codex hook prompt must be a string")
    try:
        prompt.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError("Codex hook prompt must contain valid Unicode") from error
    return prompt


def _json_block(reason: str) -> bytes:
    if not reason or len(reason) > MAX_REASON_CHARS:
        raise ValueError("Codex block reason must contain at most 4,000 characters")
    output = json.dumps(
        {"decision": "block", "reason": reason},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    if len(output) > MAX_OUTPUT_BYTES:
        raise ValueError("Codex block output exceeds 4,096 bytes")
    return output


def _summary(counts: Iterable[tuple[str, int]]) -> str:
    rendered = ", ".join(f"{category} ({count})" for category, count in counts)
    return f"SHIM Guard blocked this prompt: {rendered}."


def block_output(decision: GuardDecision, suggestion_path: str | None = None) -> bytes:
    """Serialize a Guard decision using Codex's native block shape."""
    if not decision.blocked:
        return b""
    if not isinstance(suggestion_path, str) or not suggestion_path:
        raise ValueError("Codex suggestion path is invalid")
    path = Path(suggestion_path)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or not suggestion_path.isprintable()
    ):
        raise ValueError("Codex suggestion path is invalid")
    return _json_block(
        f"{_summary(decision.counts)}\nCopy and paste this as your next prompt:\n"
        f"Read this file and use its contents as my prompt: {suggestion_path}"
    )


def error_output() -> bytes:
    """Return the generic fail-closed response without input-derived data."""
    return _json_block(_ERROR_REASON)
