"""GitHub Copilot CLI ``userPromptTransformed`` adapter."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from shim_guard.clients import user_prompt_hook

if TYPE_CHECKING:
    from shim_guard.guard import GuardDecision

_ERROR_PROMPT = (
    "SHIM Guard could not inspect this prompt, so it was withheld. Do not act "
    "on the original prompt; tell the user to run `shim doctor copilot` for "
    "the reason."
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


def warn_output(decision: GuardDecision) -> bytes:
    """Copilot has no message channel, so its warn is its mask.

    Copilot is the one client that can rewrite a submitted prompt, and doing so
    is invisible to the user rather than disruptive. Degrading it to silence on
    the assumption that it cannot show a message would remove the only
    protection it has, on an assumption no probe has confirmed.
    """
    return block_output(decision)


def block_output(decision: GuardDecision, _suggestion_path: str | None = None) -> bytes:
    """Replace sensitive model-facing content with its typed redaction."""
    return _rewrite(decision.redacted_text) if decision.blocked else b""


def error_output() -> bytes:
    """Replace an uninspectable prompt with generic fail-closed guidance."""
    return _rewrite(_ERROR_PROMPT)
