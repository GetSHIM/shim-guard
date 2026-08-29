"""Isolated client-native prompt-hook runner."""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import tempfile
import warnings
from collections.abc import Iterator
from pathlib import Path

MAX_INPUT_BYTES = 1_000_000
_PROMPT_EVENT = "UserPromptSubmit"
_PROMPT_EVENTS = frozenset({_PROMPT_EVENT, "userPromptTransformed"})
HOOK_DEADLINE_SECONDS = 25
_ERROR_OUTPUT = (
    b'{"decision":"block","reason":"SHIM Guard could not safely inspect this '
    b'prompt. Try again or run `shim scan` locally."}'
)
_CLAUDE_ERROR_OUTPUT = (
    b'{"decision":"block","reason":"SHIM Guard could not safely inspect this '
    b'prompt. Try again or run `shim scan` locally.","suppressOriginalPrompt":true}'
)
_COPILOT_ERROR_OUTPUT = (
    b'{"modifiedTransformedPrompt":"SHIM Guard could not safely inspect this '
    b"prompt. Do not act on the original prompt; tell the user to try again or "
    b'run `shim scan` locally."}'
)


def _error_output(client: str) -> bytes:
    if client == "claude":
        return _CLAUDE_ERROR_OUTPUT
    if client == "copilot":
        return _COPILOT_ERROR_OUTPUT
    return _ERROR_OUTPUT


@contextlib.contextmanager
def _silence_dependencies() -> Iterator[None]:
    """Discard Python and file-descriptor output from imported dependencies."""
    with open(os.devnull, "w", encoding="utf-8") as sink:
        stdout_fd = sys.stdout.fileno()
        stderr_fd = sys.stderr.fileno()
        saved_stdout = os.dup(stdout_fd)
        saved_stderr = os.dup(stderr_fd)
        try:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(sink.fileno(), stdout_fd)
            os.dup2(sink.fileno(), stderr_fd)
            with (
                contextlib.redirect_stdout(sink),
                contextlib.redirect_stderr(sink),
                warnings.catch_warnings(),
            ):
                warnings.simplefilter("ignore")
                yield
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(saved_stdout, stdout_fd)
            os.dup2(saved_stderr, stderr_fd)
            os.close(saved_stdout)
            os.close(saved_stderr)


@contextlib.contextmanager
def _deadline() -> Iterator[None]:
    def expire(_signal_number: int, _frame: object) -> None:
        raise TimeoutError("SHIM Guard hook deadline exceeded")

    previous = signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, HOOK_DEADLINE_SECONDS)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _write_redacted_prompt(text: str) -> str:
    stream = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="shim-guard-redacted-",
        suffix=".txt",
        delete=False,
    )
    path = Path(stream.name)
    try:
        with stream:
            if not path.is_absolute() or not str(path).isprintable():
                raise ValueError("temporary suggestion path is invalid")
            os.fchmod(stream.fileno(), 0o600)
            stream.write(text)
    except Exception:
        with contextlib.suppress(OSError):
            path.unlink()
        raise
    return str(path)


def _event_name(raw: bytes) -> str:
    """Return the event a payload names, without trusting it to be well formed.

    Cheap and defensive: a full parse happens inside the silencer, and a
    payload that does not name an event is the prompt event, which is what
    every already-installed hook fragment sends.
    """
    import json

    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return ""
    if not isinstance(document, dict):
        return ""
    event = document.get("hook_event_name")
    return event if isinstance(event, str) else ""


def _tool_output(raw: bytes, client: str, event: str) -> bytes:
    """Handle one tool event through the client-by-event matrix."""
    from shim_guard.config import load_policy
    from shim_guard.events.pipeline import process
    from shim_guard.guard import evaluate

    policy = load_policy()

    def scan(text: str):
        return evaluate(text, policy.entities)

    def mode_for(direction: str, tool: str) -> str:
        return policy.mode_for(direction, tool, event)

    return process(client, raw, mode_for, scan).output


def _output(raw: bytes, client: str = "codex") -> bytes:
    if len(raw) > MAX_INPUT_BYTES:
        return _error_output(client)

    try:
        with _silence_dependencies():
            if client == "codex":
                from shim_guard.clients.codex.hook import (
                    block_output,
                    error_output,
                    parse_input,
                    warn_output,
                )
            elif client == "claude":
                from shim_guard.clients.claude.hook import (
                    block_output,
                    error_output,
                    parse_input,
                    warn_output,
                )
            elif client == "copilot":
                from shim_guard.clients.copilot.hook import (
                    block_output,
                    error_output,
                    parse_input,
                    warn_output,
                )
            else:
                return _error_output(client)

            try:
                event = _event_name(raw)
                if event and event not in _PROMPT_EVENTS:
                    return _tool_output(raw, client, event)

                prompt = parse_input(raw)
                from shim_guard.config import load_policy
                from shim_guard.guard import evaluate

                policy = load_policy()
                decision = evaluate(prompt, policy.entities)
                if not decision.blocked or client == "copilot":
                    return (
                        warn_output(decision)
                        if client == "copilot"
                        else block_output(decision)
                    )
                mode = policy.mode_for("user-prompt", event=event or _PROMPT_EVENT)
                if mode != "enforce":
                    # The shipped default. Refusing a sentence someone just
                    # typed is the most disruptive thing this product can do,
                    # and no client offers a prompt-rewrite field.
                    return b"" if mode == "observe" else warn_output(decision)
                suggestion_path = _write_redacted_prompt(decision.redacted_text)
                try:
                    return block_output(decision, suggestion_path)
                except Exception:
                    with contextlib.suppress(OSError):
                        Path(suggestion_path).unlink()
                    raise
            except Exception:
                return error_output()
    except Exception:
        return _error_output(client)


def main() -> None:
    """Read one hook event and write the selected client's native decision."""
    arguments = sys.argv[1:]
    client = "codex" if not arguments else arguments[0]
    if len(arguments) > 1:
        client = ""
    try:
        with _deadline():
            raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
            output = _output(raw, client)
        sys.stdout.buffer.write(output)
    except Exception:
        try:
            sys.stdout.buffer.write(_error_output(client))
        except Exception:
            pass


if __name__ == "__main__":
    main()
