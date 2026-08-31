"""Verified Claude Code tool-event capabilities and native responses."""

from __future__ import annotations

import json

from shim_guard.events.pipeline import Adapter, Capabilities
from shim_guard.policy import ALLOW, DENY, MASK, REPORT

MAX_OUTPUT_BYTES = 1_000_000
_DENY_REASON = "SHIM Guard: sensitive data detected; this call was not allowed."
_ERROR_MESSAGE = "shim: this tool event could not be inspected and was not modified."
_REWRITE = Capabilities(can_rewrite=True, can_report=True)


def _dump(document: dict) -> bytes:
    output = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    if len(output) > MAX_OUTPUT_BYTES:
        raise ValueError("hook output exceeds the safe limit")
    return output


def _specific(event: str, **fields) -> dict:
    return {"hookSpecificOutput": dict({"hookEventName": event}, **fields)}


def pre_tool_use(action: str, payload: dict, message: str) -> bytes:
    if action == ALLOW:
        return b""
    if action == REPORT:
        return _dump({"systemMessage": message})
    if action == MASK:
        return _dump(
            _specific("PreToolUse", permissionDecision="allow", updatedInput=payload)
        )
    if action == DENY:
        return _dump(
            _specific(
                "PreToolUse",
                permissionDecision="deny",
                permissionDecisionReason=message or _DENY_REASON,
            )
        )
    raise ValueError("unsupported action")


def post_tool_use(action: str, payload: dict, message: str) -> bytes:
    if action == ALLOW:
        return b""
    if action == REPORT:
        return _dump({"systemMessage": message})
    if action == MASK:
        return _dump(_specific("PostToolUse", updatedToolOutput=payload))
    if action == DENY:
        raise ValueError("a tool result cannot be denied")
    raise ValueError("unsupported action")


def error_output() -> bytes:
    """Report a failed inspection without denying the tool call."""
    return _dump({"systemMessage": _ERROR_MESSAGE})


TOOL_EVENTS = {
    "PreToolUse": Adapter("claude", "PreToolUse", "tool_input", _REWRITE, pre_tool_use),
    "PostToolUse": Adapter(
        "claude", "PostToolUse", "tool_response", _REWRITE, post_tool_use
    ),
}
INSTALLED_EVENTS = tuple(sorted(TOOL_EVENTS))


def coverage() -> tuple:
    """Return verified tool-event facts for ``shim doctor``."""
    return tuple(
        {
            "event": event,
            "sees": TOOL_EVENTS[event].root,
            "can_mask": TOOL_EVENTS[event].capabilities.can_rewrite,
            "can_report": TOOL_EVENTS[event].capabilities.can_report,
            "verified": True,
            "installed": True,
        }
        for event in INSTALLED_EVENTS
    )
