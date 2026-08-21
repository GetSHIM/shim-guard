"""Isolated Codex hook runner."""

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
HOOK_DEADLINE_SECONDS = 25
_ERROR_OUTPUT = (
    b'{"decision":"block","reason":"SHIM Guard could not safely inspect this '
    b'prompt. Try again or run `shim scan` locally."}'
)


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
            stream.write(text)
    except Exception:
        with contextlib.suppress(OSError):
            path.unlink()
        raise
    return str(path)


def _output(raw: bytes) -> bytes:
    if len(raw) > MAX_INPUT_BYTES:
        return _ERROR_OUTPUT

    try:
        with _silence_dependencies():
            from shim_guard.clients.codex.hook import (
                block_output,
                error_output,
                parse_input,
            )

            try:
                prompt = parse_input(raw)
                from shim_guard.config import load_entities
                from shim_guard.guard import evaluate

                decision = evaluate(prompt, load_entities())
                if not decision.blocked:
                    return block_output(decision)
                suggestion_path = None
                try:
                    suggestion_path = _write_redacted_prompt(decision.redacted_text)
                    return block_output(decision, suggestion_path)
                except Exception:
                    if suggestion_path:
                        with contextlib.suppress(OSError):
                            Path(suggestion_path).unlink()
                    raise
            except Exception:
                return error_output()
    except Exception:
        return _ERROR_OUTPUT


def main() -> None:
    """Read one hook event and write its Codex-native decision."""
    try:
        with _deadline():
            raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
            output = _output(raw)
        sys.stdout.buffer.write(output)
    except Exception:
        try:
            sys.stdout.buffer.write(_ERROR_OUTPUT)
        except Exception:
            pass


if __name__ == "__main__":
    main()
