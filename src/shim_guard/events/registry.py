"""The (client, event) matrix.

Adding a client or an event is a registry entry plus fixtures, not a new branch
in shared code. Each entry declares three things: where the text lives in that
payload, what the client can do about it, and how to encode the answer.

`UserPromptSubmit` is deliberately absent. It predates this matrix, has its own
temporary-redaction behaviour, and is dispatched by `hook.py` exactly as before
so that every already-installed hook fragment keeps working untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import adapters
from .adapters import Capabilities

TOOL_KEY = "tool_name"


@dataclass(frozen=True)
class Adapter:
    """One cell of the client-by-event matrix."""

    __slots__ = ("client", "event", "root", "capabilities", "encode", "verified")

    client: str
    event: str
    #: The payload key whose value carries the text to scan.
    root: str
    capabilities: Capabilities
    encode: Callable[[str, dict, str], bytes]
    #: Whether the mutation shape was confirmed against a running client.
    verified: bool


_CLAUDE_REWRITE = Capabilities(can_rewrite=True, can_report=True)
_REPORT_ONLY = Capabilities(can_rewrite=False, can_report=True)

ADAPTERS = {
    ("claude", "PreToolUse"): Adapter(
        "claude",
        "PreToolUse",
        "tool_input",
        _CLAUDE_REWRITE,
        adapters.claude_pre_tool_use,
        True,
    ),
    ("claude", "PostToolUse"): Adapter(
        "claude",
        "PostToolUse",
        "tool_response",
        _CLAUDE_REWRITE,
        adapters.claude_post_tool_use,
        True,
    ),
    # A failed tool carries `error` and no `tool_response` at all, and a failing
    # command frequently echoes the credential it failed with. No mutation field
    # is documented for it, so it reports.
    ("claude", "PostToolUseFailure"): Adapter(
        "claude",
        "PostToolUseFailure",
        "error",
        _REPORT_ONLY,
        adapters.claude_post_tool_use_failure,
        False,
    ),
    ("codex", "PreToolUse"): Adapter(
        "codex",
        "PreToolUse",
        "tool_input",
        _REPORT_ONLY,
        adapters.codex_report_only,
        False,
    ),
    ("codex", "PostToolUse"): Adapter(
        "codex",
        "PostToolUse",
        "tool_response",
        _REPORT_ONLY,
        adapters.codex_report_only,
        False,
    ),
    ("copilot", "preToolUse"): Adapter(
        "copilot",
        "preToolUse",
        "args",
        _REPORT_ONLY,
        adapters.copilot_report_only,
        False,
    ),
    ("copilot", "postToolUse"): Adapter(
        "copilot",
        "postToolUse",
        "result",
        _REPORT_ONLY,
        adapters.copilot_report_only,
        False,
    ),
}

#: Only combinations whose mutation shape was confirmed against a running
#: client are installed by default. The rest are implemented and fixture-tested
#: so that enabling them is a registry flag once a probe confirms them.
INSTALLED = tuple(key for key, entry in sorted(ADAPTERS.items()) if entry.verified)


def adapter(client: str, event: str) -> Adapter:
    """Return the adapter for one combination, or raise."""
    try:
        return ADAPTERS[(client, event)]
    except KeyError:
        raise ValueError("unsupported client and event combination") from None


def events_for(client: str) -> tuple:
    """Return the tool events this client supports, in a stable order."""
    return tuple(event for (name, event) in sorted(ADAPTERS) if name == client)


def coverage(client: str) -> tuple:
    """Return one row per event for the `shim doctor` coverage table."""
    rows = []
    for event in events_for(client):
        entry = ADAPTERS[(client, event)]
        rows.append(
            {
                "event": event,
                "sees": entry.root,
                "can_mask": entry.capabilities.can_rewrite,
                "can_report": entry.capabilities.can_report,
                "verified": entry.verified,
                "installed": (client, event) in INSTALLED,
            }
        )
    return tuple(rows)
