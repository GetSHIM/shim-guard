"""GitHub Copilot CLI ``userPromptTransformed`` adapter."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from shim_guard.clients import user_prompt_hook

if TYPE_CHECKING:
    from shim_guard.guard import GuardDecision

_ERROR_PROMPT = (
    "SHIM Guard could not safely inspect this prompt. Do not act on the original "
    "prompt; tell the user to try again or run `shim scan` locally."
)


def parse_input(raw: bytes) -> str:
    """Return Copilot's model-facing transformed prompt."""
    payload = user_prompt_hook.parse_object(raw)
    prompt = payload.get("prompt")
    transformed = payload.get("transformedPrompt")
    if not isinstance(prompt, str) or not isinstance(transformed, str):
        raise ValueError("Copilot prompt-hook fields must be strings")
    try:
        prompt.encode("utf-8", errors="strict")
        transformed.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError(
            "Copilot prompt-hook fields must contain valid Unicode"
        ) from error
    return transformed


def _rewrite(text: str) -> bytes:
    if not text:
        raise ValueError("Copilot prompt rewrite must not be empty")
    output = json.dumps(
        {"modifiedTransformedPrompt": text},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return output


def block_output(decision: GuardDecision, _suggestion_path: str | None = None) -> bytes:
    """Replace sensitive model-facing content with its typed redaction."""
    return _rewrite(decision.redacted_text) if decision.blocked else b""


def error_output() -> bytes:
    """Replace an uninspectable prompt with generic fail-closed guidance."""
    return _rewrite(_ERROR_PROMPT)
