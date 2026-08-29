"""Native response encoders, one per client and event.

Every shape here for Claude Code was verified against the running client on
29 August 2026 rather than taken from documentation, because the shared context
records that documentation and recall both produced wrong answers for these
fields. `docs/probe-2026-08.md` has the transcripts: a `PreToolUse` hook
rewrote `echo ORIGINAL-VALUE` into `echo SHIM-REWRITE-OK` and the rewritten
command is what ran, and a `PostToolUse` hook replaced a file's contents and the
model reported the replacement rather than what was on disk.

Codex and Copilot tool-event encoders follow their published shapes and are
covered by fixtures, but no probe has confirmed them, so the registry ships
them at `warn` only. An unverified rewrite is worse than no rewrite: it fails
silently and the documentation would claim protection we cannot demonstrate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .policy import ALLOW, DENY, MASK, REPORT

MAX_OUTPUT_BYTES = 1_000_000
_DENY_REASON = "SHIM Guard: sensitive data detected; this call was not allowed."


@dataclass(frozen=True)
class Capabilities:
    """What the client can actually do at one event."""

    __slots__ = ("can_rewrite", "can_report")

    can_rewrite: bool
    can_report: bool


def _dump(document: dict) -> bytes:
    output = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    if len(output) > MAX_OUTPUT_BYTES:
        raise ValueError("hook output exceeds the safe limit")
    return output


def summary(counts) -> str:
    """Render entity counts without ever including a detected value."""
    return ", ".join(f"{entity} ({count})" for entity, count in counts)


def _claude_specific(event: str, **fields) -> dict:
    return {"hookSpecificOutput": dict({"hookEventName": event}, **fields)}


def claude_pre_tool_use(action: str, payload: dict, message: str) -> bytes:
    if action == ALLOW:
        return b""
    if action == REPORT:
        return _dump({"systemMessage": message})
    if action == MASK:
        return _dump(
            _claude_specific(
                "PreToolUse", permissionDecision="allow", updatedInput=payload
            )
        )
    if action == DENY:
        return _dump(
            _claude_specific(
                "PreToolUse",
                permissionDecision="deny",
                permissionDecisionReason=message or _DENY_REASON,
            )
        )
    raise ValueError("unsupported action")


def claude_post_tool_use(action: str, payload: dict, message: str) -> bytes:
    if action == ALLOW:
        return b""
    if action == REPORT:
        return _dump({"systemMessage": message})
    if action == MASK:
        return _dump(_claude_specific("PostToolUse", updatedToolOutput=payload))
    if action == DENY:
        # Inbound payloads are rewritable, so policy never reaches DENY here;
        # refusing loudly beats inventing a response shape.
        raise ValueError("a tool result cannot be denied")
    raise ValueError("unsupported action")


def claude_post_tool_use_failure(action: str, payload, message: str) -> bytes:
    """A failed tool's error text: reportable, with no documented rewrite."""
    if action == ALLOW:
        return b""
    if action == REPORT:
        return _dump({"systemMessage": message})
    raise ValueError("unsupported action")


def codex_report_only(action: str, payload: dict, message: str) -> bytes:
    """Codex tool events, until a probe confirms its mutation fields."""
    if action == ALLOW:
        return b""
    if action == REPORT:
        return _dump({"systemMessage": message})
    raise ValueError("unsupported action")


def copilot_report_only(action: str, payload: dict, message: str) -> bytes:
    """Copilot tool events, until a probe confirms its mutation fields."""
    if action == ALLOW:
        return b""
    if action == REPORT:
        return _dump({"systemMessage": message})
    raise ValueError("unsupported action")
