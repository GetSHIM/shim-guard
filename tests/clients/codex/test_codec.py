from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from shim_guard.clients.codex.hook import (
    MAX_OUTPUT_BYTES,
    MAX_REASON_CHARS,
    block_output,
    error_output,
    parse_input,
)


@dataclass(frozen=True)
class Decision:
    blocked: bool
    counts: tuple[tuple[str, int], ...] = ()
    redacted_text: str = ""


def test_parse_exact_codex_prompt_contract() -> None:
    raw = (
        b'{"session_id":"thread","hook_event_name":"UserPromptSubmit",'
        b'"turn_id":"turn","prompt":"hello \\ud83d\\udc4b"}'
    )
    assert parse_input(raw) == "hello 👋"


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"[]",
        b'{"hook_event_name":"Stop","prompt":"hello"}',
        b'{"hook_event_name":"UserPromptSubmit"}',
        b'{"hook_event_name":"UserPromptSubmit","prompt":false}',
        b'{"hook_event_name":"UserPromptSubmit","prompt":"a","prompt":"b"}',
        b'{"hook_event_name":"UserPromptSubmit","prompt":"a","number":NaN}',
        b'{"hook_event_name":"UserPromptSubmit","prompt":"\\ud800"}',
        b"\xff",
    ],
)
def test_parse_rejects_hostile_payloads(raw: bytes) -> None:
    with pytest.raises(ValueError):
        parse_input(raw)


def test_safe_decision_emits_nothing() -> None:
    assert block_output(Decision(False)) == b""


def test_block_is_exact_compact_native_json() -> None:
    output = block_output(
        Decision(True, (("EMAIL", 1), ("SECRET", 2)), "Email <EMAIL_1>; <SECRET_1>")
    )
    assert output == (
        b'{"decision":"block","reason":"SHIM Guard blocked this prompt: '
        b"EMAIL (1), SECRET (2).\\nReview and resubmit this typed redacted "
        b'suggestion:\\nEmail <EMAIL_1>; <SECRET_1>"}'
    )


def test_oversized_suggestion_is_not_emitted() -> None:
    output = block_output(Decision(True, (("SECRET", 1),), "x" * MAX_REASON_CHARS))
    decoded = json.loads(output)
    assert decoded == {
        "decision": "block",
        "reason": (
            "SHIM Guard blocked this prompt: SECRET (1). "
            "Run `shim redact` to create a typed redacted suggestion."
        ),
    }
    assert len(decoded["reason"]) <= MAX_REASON_CHARS
    assert "x" not in decoded["reason"]

    unicode_output = block_output(
        Decision(True, (("SECRET", 1),), "😀" * (MAX_OUTPUT_BYTES // 2))
    )
    assert len(unicode_output) <= MAX_OUTPUT_BYTES
    assert "😀" not in json.loads(unicode_output)["reason"]


def test_error_block_is_generic_and_compact() -> None:
    assert error_output() == (
        b'{"decision":"block","reason":"SHIM Guard could not safely inspect '
        b'this prompt. Try again or run `shim scan` locally."}'
    )
