"""Isolated client-native prompt-hook runner."""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import tempfile
import time
import warnings
from collections.abc import Iterator
from pathlib import Path

MAX_INPUT_BYTES = 1_000_000
_PROMPT_EVENT = "UserPromptSubmit"
_PROMPT_EVENTS = frozenset({_PROMPT_EVENT, "userPromptTransformed"})
#: `Stop` renders its output; `SessionEnd` does not (PRD-01, surprise 3). So
#: the summary goes at `Stop` and `SessionEnd` only cleans up after itself.
_STOP_EVENT = "Stop"
_SESSION_END_EVENT = "SessionEnd"
_STARTED = time.perf_counter()
HOOK_DEADLINE_SECONDS = 25
#: The reason has to name something that can explain the failure. The most
#: common cause is a settings file that will not parse, which blocks every
#: prompt in the session — and `shim scan` reads standard input, so it
#: reproduces nothing and says nothing about the config. `shim doctor` reports
#: exactly this, so it is what the message points at.
_ERROR_OUTPUT = (
    b'{"decision":"block","reason":"SHIM Guard could not inspect this prompt, '
    b'so it was withheld. Run `shim doctor codex` for the reason."}'
)
_CLAUDE_ERROR_OUTPUT = (
    b'{"decision":"block","reason":"SHIM Guard could not inspect this prompt, '
    b'so it was withheld. Run `shim doctor claude` for the reason.",'
    b'"suppressOriginalPrompt":true}'
)
_COPILOT_ERROR_OUTPUT = (
    b'{"modifiedTransformedPrompt":"SHIM Guard could not inspect this prompt, '
    b"so it was withheld. Do not act on the original prompt; tell the user to "
    b'run `shim doctor copilot` for the reason."}'
)


#: Failing closed means something different on a tool event than on a prompt.
#: Refusing a prompt withholds a secret the user typed. Refusing a tool event
#: does not: on `PreToolUse` a block denies the call outright, and on
#: `PostToolUse` the result has already been produced, so a block breaks the
#: user's work while protecting nothing. The event is therefore passed through
#: unchanged and the failure is said out loud, which the probe confirmed is
#: rendered at both tool events.
_TOOL_ERROR_OUTPUT = (
    b'{"systemMessage":"shim: this tool event could not be inspected and was '
    b'not modified."}'
)


def _error_output(client: str) -> bytes:
    if client == "claude":
        return _CLAUDE_ERROR_OUTPUT
    if client == "copilot":
        return _COPILOT_ERROR_OUTPUT
    return _ERROR_OUTPUT


def _tool_error_output(client: str) -> bytes:
    """Return the fail-closed response for a tool event, which never blocks."""
    if client == "copilot":
        # Copilot's tool responses have their own shape and no confirmed
        # message channel, so the only safe answer is to say nothing.
        return b""
    return _TOOL_ERROR_OUTPUT


#: Quoted event names, searched for in an over-cap payload. `main` stops
#: reading at the cap, so such a payload is a truncated prefix and will not
#: parse — but `hook_event_name` appears near the front of every real payload
#: while a large `tool_response` is what pushed it over, so the name survives
#: the truncation and the bulky field does not.
_TOOL_EVENT_MARKERS = (
    b'"PreToolUse"',
    b'"PostToolUse"',
    b'"PostToolUseFailure"',
    b'"PostToolBatch"',
    b'"Stop"',
    b'"SessionEnd"',
    b'"preToolUse"',
    b'"postToolUse"',
)


def _oversize_output(raw: bytes, client: str) -> bytes:
    """Choose which shape of refusal an unparseably large payload gets.

    Only the shape is decided here, never whether to protect: an over-cap
    payload is refused either way. Getting it wrong towards "prompt" would
    deny an ordinary large file read, so the tool markers are searched for in
    the head of the payload, where a real client puts the event name.
    """
    head = raw[:4096]
    if any(marker in head for marker in _TOOL_EVENT_MARKERS):
        return _tool_error_output(client)
    return _error_output(client)


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


def _envelope(raw: bytes) -> tuple:
    """Return ``(event, session_id, stop_active)`` from an untrusted payload.

    Cheap and defensive: a payload that does not name an event is the prompt
    event, which is what every already-installed hook fragment sends. This is
    the only parse outside the pipeline's own, so nothing here re-reads the
    payload just to learn which session it belongs to.
    """
    import json

    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return ("", "", False)
    if not isinstance(document, dict):
        return ("", "", False)
    event = document.get("hook_event_name")
    # Claude Code and Codex send `session_id`. GitHub's hooks reference
    # describes `sessionId` for Copilot, which has not been confirmed against a
    # running client — accepting both costs nothing and records nothing extra
    # if neither is present, which is what Copilot does today.
    session = document.get("session_id")
    if not isinstance(session, str):
        session = document.get("sessionId")
    return (
        event if isinstance(event, str) else "",
        session if isinstance(session, str) else "",
        bool(document.get("stop_hook_active")),
    )


def _elapsed_ms() -> int:
    """Return how long this hook process has been running.

    Measured from the moment this module was imported, which is the closest
    honest proxy for what the client waited: it includes the detector import,
    which dominates, but not the interpreter's own start-up.
    """
    return max(0, round((time.perf_counter() - _STARTED) * 1000))


def _remember(session_id: str, record, latency_ms: int, ledger: bool = False) -> None:
    """Spool one decision. A recording failure must never fail the guard."""
    if not session_id:
        return
    try:
        entry = record.as_dict()
        entry["session_id"] = session_id
        entry["latency_ms"] = latency_ms
        entry["ts"] = _timestamp()
    except Exception:
        return
    try:
        from shim_guard.session import spool

        spool.append(session_id, entry)
    except Exception:
        pass
    if not ledger:
        return
    try:
        from shim_guard.session import ledger as store

        store.append(entry)
    except Exception:
        return


def _timestamp() -> str:
    import datetime

    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _prompt_record(client, event, mode, action, decision, prompt):
    """Return the record for a prompt decision, carrying no prompt text."""
    from shim_guard.events.record import Record

    return Record(
        client=client,
        event=event or _PROMPT_EVENT,
        tool_name="",
        direction="user-prompt",
        mode=mode,
        action=action,
        entities=tuple(decision.counts),
        in_bytes=len(prompt.encode("utf-8", "replace")),
        out_bytes=0,
        fields=1 if decision.counts else 0,
    )


def _summary_output(session_id: str, stop_active: bool) -> bytes:
    """Return the session summary at `Stop`, or nothing when it is unchanged.

    `Stop` fires at the end of every assistant turn, so the summary is emitted
    only when there are records the user has not been shown yet. The body is
    the session total, not the turn's.
    """
    if not session_id or stop_active:
        return b""
    try:
        import json

        from shim_guard.session import spool, summary

        records = spool.entries(session_id)
        if len(records) <= spool.summarized(session_id):
            return b""
        text = summary.render(records, spool.capped(session_id))
        spool.mark_summarized(session_id, len(records))
        if not text:
            return b""
        return json.dumps({"systemMessage": text}, ensure_ascii=False).encode()
    except Exception:
        return b""


def _forget(session_id: str) -> bytes:
    """Delete the session spool at `SessionEnd`.

    The probe showed `SessionEnd` output is never rendered, so this event is
    good for exactly one thing, and this is it.
    """
    try:
        from shim_guard.session import spool

        spool.clear(session_id)
    except Exception:
        pass
    return b""


def _tool_output(raw: bytes, client: str, event: str, session_id: str) -> bytes:
    """Handle one tool event through the client-by-event matrix."""
    from shim_guard.config import load_policy
    from shim_guard.events.pipeline import process
    from shim_guard.guard import evaluate

    policy = load_policy()

    def mode_for(direction: str, tool: str) -> str:
        return policy.mode_for(direction, tool, event)

    def entities_for(tool: str, _event: str = "") -> tuple:
        return policy.entities_for(tool, event)

    outcome = process(client, raw, mode_for, evaluate, policy.diet, entities_for)
    _remember(session_id, outcome.record, _elapsed_ms(), policy.ledger)
    return outcome.output


def _output(raw: bytes, client: str = "codex") -> bytes:
    if len(raw) > MAX_INPUT_BYTES:
        return _oversize_output(raw, client)

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
                event, session_id, stop_active = _envelope(raw)
                if event == _STOP_EVENT:
                    return _summary_output(session_id, stop_active)
                if event == _SESSION_END_EVENT:
                    return _forget(session_id)
                if event and event not in _PROMPT_EVENTS:
                    try:
                        return _tool_output(raw, client, event, session_id)
                    except Exception:
                        return _tool_error_output(client)

                prompt = parse_input(raw)
                from shim_guard.config import load_policy
                from shim_guard.guard import evaluate

                policy = load_policy()
                decision = evaluate(prompt, policy.entities)
                mode = policy.mode_for("user-prompt", event=event or _PROMPT_EVENT)

                def keep(action: str) -> None:
                    # Building the record is part of recording, so a decision
                    # object that does not carry what the record wants must not
                    # turn a working guard into a failure.
                    try:
                        record = _prompt_record(
                            client, event, mode, action, decision, prompt
                        )
                    except Exception:
                        return
                    _remember(session_id, record, _elapsed_ms(), policy.ledger)

                if not decision.blocked or client == "copilot":
                    # Copilot is the one client that can rewrite a submitted
                    # prompt, so there it is a mask rather than a warning.
                    keep("mask" if decision.blocked else "allow")
                    return (
                        warn_output(decision)
                        if client == "copilot"
                        else block_output(decision)
                    )
                if mode != "enforce":
                    # The shipped default. Refusing a sentence someone just
                    # typed is the most disruptive thing this product can do,
                    # and no client offers a prompt-rewrite field.
                    keep("allow" if mode == "observe" else "report")
                    return b"" if mode == "observe" else warn_output(decision)
                suggestion_path = _write_redacted_prompt(decision.redacted_text)
                try:
                    output = block_output(decision, suggestion_path)
                except Exception:
                    with contextlib.suppress(OSError):
                        Path(suggestion_path).unlink()
                    raise
                keep("deny")
                return output
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
