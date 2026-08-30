"""How the hook fails, per event kind.

Failing closed means opposite things on a prompt and on a tool event, and
getting that backwards was a real bug: a malformed or over-cap tool payload
answered with the prompt's `decision: "block"`, which on `PreToolUse` denies
the tool call outright.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHIM_GUARD_SESSION_DIR", str(tmp_path / "spools"))


def _run(raw: bytes, client: str = "claude") -> bytes:
    result = subprocess.run(
        (sys.executable, "-I", "-B", "-m", "shim_guard.hook", client),
        input=raw,
        capture_output=True,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr.decode()
    return result.stdout


MALFORMED_TOOL_EVENTS = (
    b'{"hook_event_name":"PreToolUse","tool_name":123,"tool_input":{"a":"b"}}',
    b'{"hook_event_name":"PostToolUse","tool_name":123,"tool_response":{"a":"b"}}',
    b'{"hook_event_name":"PreToolUse","tool_name":{"a":1},"tool_input":{}}',
    b'{"hook_event_name":"NotARealEvent","tool_name":"Read","tool_input":{}}',
)


@pytest.mark.parametrize("raw", MALFORMED_TOOL_EVENTS)
def test_a_malformed_tool_event_is_never_answered_with_a_block(raw: bytes) -> None:
    document = json.loads(_run(raw))

    assert "decision" not in document, "a block here denies the tool call"
    assert "permissionDecision" not in document
    assert "hookSpecificOutput" not in document
    assert document["systemMessage"].startswith("shim:")
    assert "prompt" not in document["systemMessage"]


@pytest.mark.parametrize("raw", MALFORMED_TOOL_EVENTS)
def test_copilot_says_nothing_rather_than_guessing_a_shape(raw: bytes) -> None:
    assert _run(raw, "copilot") == b""


def test_a_malformed_prompt_event_still_fails_closed() -> None:
    """The prompt is the one place where refusing is the protection."""
    document = json.loads(_run(b'{"hook_event_name":"UserPromptSubmit","prompt":7}'))

    assert document["decision"] == "block"


def test_an_over_cap_tool_payload_does_not_deny_the_tool_call() -> None:
    """A one-megabyte file read is ordinary, not hostile.

    The payload is truncated at the cap and so cannot be parsed; only the
    shape of the refusal is decided, from the event name near its front.
    """
    head = b'{"hook_event_name":"PostToolUse","session_id":"s","tool_name":"Read",'
    raw = head + b'"tool_response":{"content":"' + b"A" * 1_100_000

    document = json.loads(_run(raw))

    assert "decision" not in document
    assert document["systemMessage"].startswith("shim:")


def test_an_over_cap_prompt_payload_still_fails_closed() -> None:
    raw = b'{"hook_event_name":"UserPromptSubmit","prompt":"' + b"A" * 1_100_000

    document = json.loads(_run(raw))

    assert document["decision"] == "block"


def test_a_file_name_cannot_carry_terminal_escapes_into_the_summary() -> None:
    """Checking out a repository is enough to choose a file name."""
    from shim_guard.session import spool, summary

    hostile = "/work/" + chr(27) + "[31mred" + chr(7) + "/config.env"
    _run(
        json.dumps(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "escapes",
                "tool_name": "Read",
                "tool_input": {"file_path": hostile},
                "tool_response": {
                    "type": "text",
                    "file": {"content": "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"},
                },
            }
        ).encode()
    )

    records = spool.entries("escapes")
    assert records, "the event was not recorded"
    rendered = summary.render(records) + json.dumps(records)
    assert chr(27) not in rendered
    assert chr(7) not in rendered
    assert "config.env" in summary.render(records)


def test_an_uninspectable_tool_event_is_still_recorded(monkeypatch, tmp_path) -> None:
    """Failing open must not mean failing silently.

    Blocking a tool event destroys the user's work and protects nothing — the
    result already exists — so passing it through is right. But nothing was
    written down, so `shim report` and the end-of-turn summary called the
    session clean while an unmasked payload had just gone to the model.
    Observed live on a customer CSV dense enough to exceed the finding limit.
    """
    monkeypatch.setenv("SHIM_GUARD_SESSION_DIR", str(tmp_path / "spools"))
    from shim_guard import hook
    from shim_guard.events.record import NOT_INSPECTED
    from shim_guard.session import spool

    def explode(*_args, **_kwargs):
        raise ValueError("Guard analysis exceeded the safe finding limit.")

    monkeypatch.setattr(hook, "_tool_output", explode)
    payload = json.dumps(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "dense",
            "tool_name": "Read",
            "tool_input": {"file_path": "/work/customers.csv"},
            "tool_response": {"type": "text", "content": "a@b.com"},
        }
    ).encode()

    output = hook._output(payload, "claude")

    # The payload still goes through untouched, and the client is told.
    assert b"could not be inspected" in output
    records = spool.entries("dense")
    assert len(records) == 1
    assert records[0]["action"] == "report"
    assert records[0]["tool_name"] == "Read"
    assert records[0]["note"].startswith(NOT_INSPECTED)


def test_the_summary_names_an_uninspected_event(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SHIM_GUARD_SESSION_DIR", str(tmp_path / "spools"))
    from shim_guard.events.record import NOT_INSPECTED
    from shim_guard.session import spool, summary

    spool.append(
        "dense",
        {
            "action": "report",
            "event": "PostToolUse",
            "tool_name": "Read",
            "target": "/work/customers.csv",
            "entities": {},
            "note": f"{NOT_INSPECTED}: analysis failed",
            "latency_ms": 9,
        },
    )

    text = summary.render(spool.entries("dense"))

    assert "skipped" in text
    assert "not inspected, passed through" in text
    assert "Read customers.csv" in text
    assert summary.as_json(spool.entries("dense"))["not_inspected"] == 1
