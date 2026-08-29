"""Codex ``UserPromptSubmit`` input and output adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shim_guard.clients import user_prompt_hook

if TYPE_CHECKING:
    from shim_guard.guard import GuardDecision

MAX_OUTPUT_BYTES = user_prompt_hook.MAX_OUTPUT_BYTES
MAX_REASON_CHARS = user_prompt_hook.MAX_REASON_CHARS
parse_input = user_prompt_hook.parse_input
warn_output = user_prompt_hook.warn_output


def block_output(decision: GuardDecision, suggestion_path: str | None = None) -> bytes:
    """Serialize a Guard decision using Codex's native block shape."""
    return user_prompt_hook.block_output(decision, suggestion_path)


def error_output() -> bytes:
    """Return the generic fail-closed response without input-derived data."""
    return user_prompt_hook.error_output()
