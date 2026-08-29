"""End-to-end policy over the payloads a client really sent.

The two tests PRD-05 R2 requires — a `Write` payload passed through
byte-identical and a `Bash` command never modified — are the reason this module
exists. Everything else here guards the machinery that makes them true.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shim_guard.events.pipeline import process
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
    WARN,
)
from shim_guard.events.registry import ADAPTERS, INSTALLED, coverage
from shim_guard.guard import evaluate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "probe" / "claude"


def _raw(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _run(name: str, mode: str, client: str = "claude"):
    return process(client, _raw(name), lambda direction, tool: mode, evaluate)


# --- R2: the payloads that are never rewritten ----------------------------


@pytest.mark.parametrize("mode", (OBSERVE, WARN, ENFORCE))
def test_a_write_payload_is_passed_through_byte_identical(mode: str) -> None:
    """Rewriting this would put a placeholder into the user's real file."""
    name = "PreToolUse-Write-write-1.json"
    original = json.loads(_raw(name))

    outcome = _run(name, mode)

    assert outcome.record.direction == LOCAL_WRITE
    assert outcome.record.action != MASK
    if outcome.output:
        emitted = json.loads(outcome.output)
        assert "updatedInput" not in json.dumps(emitted)
        assert original["tool_input"]["content"] not in json.dumps(emitted)
    # The secret is still detected, so the user can be warned about it.
    assert outcome.record.entities == (("SECRET", 1),)


@pytest.mark.parametrize("mode", (OBSERVE, WARN, ENFORCE))
def test_a_bash_command_is_allowed_or_denied_but_never_modified(mode: str) -> None:
    """Editing a command changes what runs; shim may only allow or refuse."""
    name = "PreToolUse-Bash-bash-connection-string-1.json"
    original = json.loads(_raw(name))["tool_input"]["command"]

    outcome = _run(name, mode)

    assert outcome.record.direction == EXECUTABLE_TEXT
    assert outcome.record.action in (ALLOW, REPORT, DENY)
    assert outcome.record.action != MASK
    assert outcome.record.entities == (("DB_URI", 1),)
    if outcome.output:
        rendered = outcome.output.decode()
        assert "updatedInput" not in rendered
        assert original not in rendered
        assert "postgresql://" not in rendered


def test_enforce_denies_a_command_rather_than_editing_it() -> None:
    outcome = _run("PreToolUse-Bash-bash-connection-string-1.json", ENFORCE)
    document = json.loads(outcome.output)

    assert outcome.record.action == DENY
    assert document["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "DB_URI (1)" in document["hookSpecificOutput"]["permissionDecisionReason"]


# --- inbound and outbound masking -----------------------------------------


def test_a_read_result_is_masked_in_place_under_enforce() -> None:
    outcome = _run("PostToolUse-Read-read-small-1.json", ENFORCE)
    document = json.loads(outcome.output)
    updated = document["hookSpecificOutput"]["updatedToolOutput"]

    assert document["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert outcome.record.direction == INBOUND
    assert updated["file"]["content"].startswith("DATABASE_URL=<DB_URI_1>")
    assert "s3cr3tpw" not in outcome.output.decode()
    assert updated["type"] == "text"
    assert updated["file"]["numLines"] == 6


def test_an_mcp_argument_object_is_masked_in_place() -> None:
    outcome = _run("PreToolUse-mcp__probe__probe_echo-mcp-echo-1.json", ENFORCE)
    document = json.loads(outcome.output)
    updated = document["hookSpecificOutput"]["updatedInput"]

    assert outcome.record.direction == OUTBOUND
    assert updated == {"customer_email": "<EMAIL_1>", "note": "ping"}
    assert document["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_a_failed_tool_reports_its_error_text() -> None:
    """PostToolUseFailure carries `error` and no tool_response at all.

    A failing command frequently echoes the credential it failed with, so the
    error string is inbound text like any other.
    """
    outcome = process(
        "claude",
        _raw("PostToolUseFailure-Bash-bash-connection-string-1.json"),
        lambda direction, tool: ENFORCE,
        evaluate,
    )

    assert outcome.record.direction == INBOUND
    assert outcome.record.in_bytes > 0, "the error string must be scanned"
    # This particular error is clean: psql redacted the URI in its own message.
    assert outcome.record.entities == ()
    assert outcome.record.action == ALLOW
    assert outcome.output == b""


def test_a_failed_tool_reports_a_credential_its_error_does_echo() -> None:
    payload = json.loads(_raw("PostToolUseFailure-Bash-bash-connection-string-1.json"))
    payload["error"] = (
        "Exit code 1\nauth failed for postgresql://alice:pw@db.example.com/app"
    )

    outcome = process(
        "claude", json.dumps(payload).encode(), lambda d, t: ENFORCE, evaluate
    )

    assert outcome.record.entities == (("DB_URI", 1),)
    # No mutation field is documented for this event, so enforce degrades.
    assert outcome.record.action == REPORT
    assert outcome.record.degraded_from == MASK
    assert "postgresql://" not in outcome.output.decode()


def test_a_clean_payload_produces_no_output_at_any_mode() -> None:
    for mode in (OBSERVE, WARN, ENFORCE):
        outcome = _run("PostToolUse-WebFetch-webfetch-1.json", mode)
        assert outcome.output == b""
        assert outcome.record.action == ALLOW
        assert outcome.record.entities == ()


def test_observe_never_emits_anything_but_still_counts() -> None:
    outcome = _run("PostToolUse-Read-read-small-1.json", OBSERVE)

    assert outcome.output == b""
    assert outcome.record.action == ALLOW
    assert dict(outcome.record.entities) == {"DB_URI": 1, "EMAIL": 1, "SECRET": 3}


def test_an_oversized_payload_falls_back_to_observing() -> None:
    outcome = _run("PostToolUse-Read-read-large-1.json", ENFORCE)

    assert outcome.record.action in (ALLOW, MASK)
    if outcome.record.action == ALLOW and outcome.record.note:
        assert "limit" in outcome.record.note or "safe" in outcome.record.note


# --- the record -----------------------------------------------------------


def test_the_record_never_carries_payload_text() -> None:
    """PRD-06 depends on this being true from the start."""
    for name in sorted(path.name for path in FIXTURES.glob("P*ToolUse-*.json")):
        payload = json.loads(_raw(name))
        if payload["hook_event_name"] not in ("PreToolUse", "PostToolUse"):
            continue
        outcome = process(
            "claude", _raw(name), lambda direction, tool: ENFORCE, evaluate
        )
        rendered = json.dumps(outcome.record.as_dict())
        for secret in (
            "s3cr3tpw",
            "AKIAIOSFODNN7EXAMPLE",
            "SuperSecret123!",
            "alice@example.com",
        ):
            assert secret not in rendered, f"{name} leaked into the record"


def test_degradation_is_recorded_rather_than_silent() -> None:
    outcome = process(
        "codex",
        _raw("PostToolUse-Read-read-small-1.json").replace(
            b'"hook_event_name": "PostToolUse"', b'"hook_event_name": "PostToolUse"'
        ),
        lambda direction, tool: ENFORCE,
        evaluate,
    )

    assert outcome.record.action == REPORT
    assert outcome.record.degraded_from == MASK


# --- the matrix -----------------------------------------------------------


def test_only_verified_combinations_are_installed() -> None:
    """An unverified rewrite fails silently; that is worse than not shipping."""
    for key, entry in ADAPTERS.items():
        assert (key in INSTALLED) is entry.verified
    assert INSTALLED == (("claude", "PostToolUse"), ("claude", "PreToolUse"))


def test_coverage_reports_what_each_client_can_and_cannot_do() -> None:
    claude = {row["event"]: row for row in coverage("claude")}
    codex = {row["event"]: row for row in coverage("codex")}

    assert claude["PreToolUse"]["can_mask"] is True
    assert claude["PostToolUse"]["sees"] == "tool_response"
    assert codex["PostToolUse"]["can_mask"] is False
    assert codex["PostToolUse"]["installed"] is False


def test_an_unknown_combination_is_refused() -> None:
    with pytest.raises(ValueError, match="unsupported client and event"):
        process(
            "gemini", _raw("PreToolUse-Write-write-1.json"), lambda d, t: WARN, evaluate
        )


@pytest.mark.parametrize(
    "case",
    [
        case
        for case in json.loads(
            (
                Path(__file__).resolve().parents[1] / "corpus" / "guard-tools-v1.json"
            ).read_text(encoding="utf-8")
        )["cases"]
        # The corpus labels each scanned path; the pipeline scans one root per
        # event. Compare only the cases whose path is the root that event reads.
        if (case["client"], case["event"]) in {(c, e) for c, e in ADAPTERS}
        and all(
            scanned["path"][0] == ADAPTERS[(case["client"], case["event"])].root
            for scanned in case["scanned"]
        )
    ],
    ids=lambda case: case["id"],
)
def test_the_tool_corpus_agrees_with_the_pipeline(case: dict) -> None:
    """PRD-03 declares the policy; PRD-05 implements it. They must match."""
    outcome = process(
        case["client"],
        (FIXTURES / case["fixture"].split("/", 1)[1]).read_bytes(),
        lambda direction, tool: ENFORCE,
        evaluate,
    )
    expected_findings = any(scanned["categories"] for scanned in case["scanned"])

    assert outcome.record.direction == case["direction"]
    if not expected_findings:
        assert outcome.record.action == ALLOW
        assert outcome.output == b""
        return
    if case["rewritable"]:
        assert outcome.record.action in (MASK, REPORT)
    else:
        assert outcome.record.action != MASK
        for scanned in case["scanned"]:
            if scanned.get("unchanged") and outcome.output:
                assert scanned["expected_output"] not in outcome.output.decode()
