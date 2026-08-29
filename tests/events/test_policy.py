"""Direction classification and the policy table are the safety rules."""

from __future__ import annotations

import pytest

from shim_guard.events import policy
from shim_guard.events.policy import (
    ALLOW,
    DENY,
    ENFORCE,
    EXECUTABLE_TEXT,
    INBOUND,
    LOCAL_WRITE,
    MASK,
    OBSERVE,
    OUTBOUND,
    REPORT,
    USER_PROMPT,
    WARN,
    decide,
    direction_for,
)


@pytest.mark.parametrize(
    ("event", "tool", "expected"),
    (
        ("UserPromptSubmit", "", USER_PROMPT),
        ("userPromptTransformed", "", USER_PROMPT),
        ("PreToolUse", "Bash", EXECUTABLE_TEXT),
        ("PreToolUse", "Shell", EXECUTABLE_TEXT),
        ("PreToolUse", "Write", LOCAL_WRITE),
        ("PreToolUse", "Edit", LOCAL_WRITE),
        ("PreToolUse", "apply_patch", LOCAL_WRITE),
        ("PreToolUse", "NotebookEdit", LOCAL_WRITE),
        ("PreToolUse", "WebFetch", OUTBOUND),
        ("PreToolUse", "mcp__server__tool", OUTBOUND),
        ("preToolUse", "Read", OUTBOUND),
        ("PostToolUse", "Read", INBOUND),
        ("PostToolUse", "Bash", INBOUND),
        ("PostToolUse", "Write", INBOUND),
        ("PostToolUseFailure", "Bash", INBOUND),
        ("postToolUse", "mcp__server__tool", INBOUND),
    ),
)
def test_direction_is_decided_by_payload_kind_not_tool_name(
    event: str, tool: str, expected: str
) -> None:
    assert direction_for(event, tool) == expected


def test_unknown_events_are_refused_rather_than_guessed() -> None:
    with pytest.raises(ValueError, match="unsupported hook event"):
        direction_for("SessionStart", "Read")


@pytest.mark.parametrize("direction", (LOCAL_WRITE, EXECUTABLE_TEXT, USER_PROMPT))
def test_unrewritable_directions_are_never_masked(direction: str) -> None:
    """The single most important rule in the tool policy."""
    assert policy.REWRITABLE[direction] is False
    for mode in (OBSERVE, WARN, ENFORCE):
        assert decide(direction, mode).action != MASK


@pytest.mark.parametrize("direction", (OUTBOUND, INBOUND))
def test_rewritable_directions_mask_only_under_enforce(direction: str) -> None:
    assert decide(direction, OBSERVE).action == ALLOW
    assert decide(direction, WARN).action == REPORT
    assert decide(direction, ENFORCE).action == MASK


@pytest.mark.parametrize("direction", (LOCAL_WRITE, EXECUTABLE_TEXT, USER_PROMPT))
def test_unrewritable_directions_deny_under_enforce(direction: str) -> None:
    assert decide(direction, ENFORCE).action == DENY


def test_a_client_that_cannot_rewrite_degrades_and_records_it() -> None:
    """Codex has no surgical result rewrite; it must warn, not silently pass."""
    decision = decide(INBOUND, ENFORCE, can_rewrite=False)

    assert decision.action == REPORT
    assert decision.degraded is True
    assert decision.degraded_from == MASK
    assert "rewrite" in decision.reason


def test_a_client_that_can_neither_rewrite_nor_report_says_so() -> None:
    decision = decide(INBOUND, ENFORCE, can_rewrite=False, can_report=False)

    assert (decision.action, decision.degraded_from) == (ALLOW, MASK)
    assert decision.reason


def test_warn_degrades_when_the_client_cannot_show_a_message() -> None:
    decision = decide(OUTBOUND, WARN, can_report=False)

    assert (decision.action, decision.degraded_from) == (ALLOW, REPORT)


def test_observe_never_acts_whatever_the_client_supports() -> None:
    for direction in policy.DIRECTIONS:
        decision = decide(direction, OBSERVE, can_rewrite=True, can_report=True)
        assert decision.action == ALLOW
        assert decision.degraded is False


def test_unknown_directions_and_modes_are_refused() -> None:
    with pytest.raises(ValueError, match="unsupported policy direction"):
        decide("sideways", WARN)
    with pytest.raises(ValueError, match="unsupported policy mode"):
        decide(INBOUND, "paranoid")
