"""Claude Code ``UserPromptSubmit`` input and output adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shim_guard.clients import user_prompt_hook

if TYPE_CHECKING:
    from shim_guard.guard import GuardDecision

parse_input = user_prompt_hook.parse_input


def block_output(decision: GuardDecision, suggestion_path: str | None = None) -> bytes:
    """Serialize a Guard decision using Claude Code's native block shape."""
    return user_prompt_hook.block_output(
        decision, suggestion_path, suppress_original_prompt=True
    )


def error_output() -> bytes:
    """Return the generic fail-closed response without input-derived data."""
    return user_prompt_hook.error_output(suppress_original_prompt=True)
