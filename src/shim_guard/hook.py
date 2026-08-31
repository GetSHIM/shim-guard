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
#: Live capability probing found that `Stop` renders its output while
#: `SessionEnd` does not. So the summary goes at `Stop` and `SessionEnd` only
#: cleans up after itself.
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


def _error_output(client: str) -> bytes:
    if client == "claude":
        return _CLAUDE_ERROR_OUTPUT
    if client == "copilot":
        return _COPILOT_ERROR_OUTPUT
    return _ERROR_OUTPUT


def _tool_error_output(client: str, event: str) -> bytes:
    """Report a verified Claude tool failure without denying the call."""
    if client != "claude":
        return b""
    try:
        from shim_guard.clients.claude import tool_events

        if event not in tool_events.TOOL_EVENTS:
            return b""
        return tool_events.error_output()
    except Exception:
        return b""


#: Quoted event names, searched for in a payload that will not parse. `main`
#: stops reading at the cap, so an over-cap payload is a truncated prefix — but
#: `hook_event_name` appears near the front of every real payload while a large
#: `tool_response` is what pushed it over, so the name survives the truncation
#: and the bulky field does not.
_TOOL_EVENT_NAMES = (
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PostToolBatch",
    "Stop",
    "SessionEnd",
    "preToolUse",
    "postToolUse",
)
_TOOL_EVENT_MARKERS = tuple(f'"{event}"'.encode() for event in _TOOL_EVENT_NAMES)


def _refusal_output(raw: bytes, client: str) -> bytes:
    """Choose which shape of refusal an unhandled payload gets.

    Only the shape is decided here, never whether to protect: the payload is
    refused either way. Getting it wrong towards "prompt" answers a tool event
    with `{"decision": "block"}`, which on `PreToolUse` denies the call — the
    one thing the fail-closed rule above says must never happen, because the
    user loses work and nothing is protected by taking it.

    Every path that gives up outside the pipeline comes through here: an
    over-cap payload, the hook deadline, and a dependency that would not
    import. The event name settles it when the payload parses; when it does
    not — which is what an over-cap payload always is — the tool markers are
    searched for in the head, where a real client puts the event name.
    """
    try:
        event, _session, _stop = _envelope(raw)
    except Exception:
        event = ""
    if event:
        return (
            _error_output(client)
            if event in _PROMPT_EVENTS
            else _tool_error_output(client, event)
        )
    for event, marker in zip(_TOOL_EVENT_NAMES, _TOOL_EVENT_MARKERS):
        if marker in raw[:4096]:
            return _tool_error_output(client, event)
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


#: How long a redacted-prompt suggestion is kept before `SessionEnd` sweeps it.
#: The file has to outlive the turn that produced it — the user is told to read
#: it as their next prompt — so this is generous. Past a day it is litter.
SUGGESTION_MAX_AGE_SECONDS = 24 * 60 * 60
_SUGGESTION_PREFIX = "shim-guard-redacted-"
_SUGGESTION_SUFFIX = ".txt"


def _sweep_suggestions() -> None:
    """Delete stale redacted-prompt files. Never raises.

    `_write_redacted_prompt` cannot clean up after itself: the path it returns
    is handed to the client so the user can read it as their next prompt, so it
    has to outlive the process that wrote it. Nothing else deleted it, which
    left a file in the temporary directory for every prompt ever blocked.

    The age bound is what makes this safe to run from one session: another
    client blocking a prompt right now has a file here too, and its user has
    not read it yet.
    """
    now = time.time()
    with contextlib.suppress(OSError):
        root = Path(tempfile.gettempdir())
        for path in root.glob(f"{_SUGGESTION_PREFIX}*{_SUGGESTION_SUFFIX}"):
            with contextlib.suppress(OSError):
                if now - path.stat().st_mtime > SUGGESTION_MAX_AGE_SECONDS:
                    path.unlink()


def _write_redacted_prompt(text: str) -> str:
    stream = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=_SUGGESTION_PREFIX,
        suffix=_SUGGESTION_SUFFIX,
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


def _prompt_record(client, event, mode, action, decision, prompt):
    """Return the record for a prompt decision, carrying no prompt text."""
    from shim_guard.session.record import Record

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
    """Delete what this session left on disk, at `SessionEnd`.

    The probe showed `SessionEnd` output is never rendered, so this event is
    good for exactly one thing: cleaning up. That is the spool, and the
    redacted-prompt files no other path could delete.
    """
    _sweep_suggestions()
    try:
        from shim_guard.session import spool

        spool.clear(session_id)
    except Exception:
        pass
    return b""


def _uninspected(raw: bytes, client: str, event: str, session_id: str) -> None:
    """Record a tool event that could not be inspected. Never raises.

    Failing open here is deliberate — blocking a tool event destroys the user's
    work while protecting nothing, because the result already exists. Failing
    open *silently* is not: nothing was written down, so `shim report` and the
    end-of-turn summary said the session was clean while an unmasked payload
    had just gone to the model. Observed live on a customer CSV dense enough to
    exceed the finding limit.
    """
    try:
        import json

        from shim_guard.session import remember
        from shim_guard.session.record import (
            NOT_INSPECTED,
            UNKNOWN_TOOL_LABEL,
            UNSUPPORTED_EVENT_LABEL,
            Record,
            display_label,
        )

        tool = ""
        try:
            document = json.loads(raw.decode("utf-8", "replace"))
            if isinstance(document, dict) and isinstance(
                document.get("tool_name"), str
            ):
                tool = document["tool_name"]
        except Exception:
            pass
        event_label = event if event in _TOOL_EVENT_NAMES else UNSUPPORTED_EVENT_LABEL
        tool_label = display_label(tool, UNKNOWN_TOOL_LABEL)
        if tool_label != UNKNOWN_TOOL_LABEL:
            try:
                from shim_guard.guard import evaluate

                decision = evaluate(tool_label)
                if decision.counts:
                    tool_label = display_label(
                        decision.redacted_text, UNKNOWN_TOOL_LABEL
                    )
            except Exception:
                tool_label = UNKNOWN_TOOL_LABEL
        remember(
            session_id,
            Record(
                client=client,
                event=event_label,
                tool_name=tool_label,
                direction="",
                mode="",
                action="report",
                note=f"{NOT_INSPECTED}: analysis failed; passed through unchanged",
            ),
            _elapsed_ms(),
            _policy_ledger(),
        )
    except Exception:
        pass


def _policy_ledger() -> bool:
    try:
        from shim_guard.config import load_policy

        return load_policy().ledger
    except Exception:
        return False


def _tool_output(raw: bytes, entry, event: str, session_id: str) -> bytes:
    """Handle one tool event through its client-owned adapter."""
    from shim_guard.config import load_policy
    from shim_guard.events.pipeline import process
    from shim_guard.guard import evaluate
    from shim_guard.session import remember

    policy = load_policy()

    def mode_for(direction: str, tool: str) -> str:
        return policy.mode_for(direction, tool, event)

    def entities_for(tool: str, _event: str = "") -> tuple:
        return policy.entities_for(tool, event)

    outcome = process(entry, raw, mode_for, evaluate, policy.diet, entities_for)
    remember(session_id, outcome.record, _elapsed_ms(), policy.ledger)
    return outcome.output


def _output(raw: bytes, client: str = "codex") -> bytes:
    if len(raw) > MAX_INPUT_BYTES:
        return _refusal_output(raw, client)

    try:
        with _silence_dependencies():
            tool_event_adapters = None
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
                from shim_guard.clients.claude.tool_events import TOOL_EVENTS

                tool_event_adapters = TOOL_EVENTS
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
                    if tool_event_adapters is None:
                        return b""
                    entry = tool_event_adapters.get(event)
                    if entry is None:
                        return b""
                    try:
                        return _tool_output(raw, entry, event, session_id)
                    except Exception:
                        _uninspected(raw, client, entry.event, session_id)
                        return _tool_error_output(client, entry.event)

                prompt = parse_input(raw)
                from shim_guard.config import load_policy
                from shim_guard.guard import evaluate
                from shim_guard.session import remember

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
                    remember(session_id, record, _elapsed_ms(), policy.ledger)

                if not decision.blocked:
                    keep("allow")
                    return b""
                if mode == "observe":
                    keep("allow")
                    return b""
                if client == "copilot":
                    # Copilot can rewrite the model-facing prompt, so its
                    # default warning is a mask rather than a silent report.
                    keep("mask")
                    return warn_output(decision)
                if mode != "enforce":
                    # The shipped default. Refusing a sentence someone just
                    # typed is the most disruptive thing this product can do,
                    # and no client offers a prompt-rewrite field.
                    keep("report")
                    return warn_output(decision)
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
        return _refusal_output(raw, client)


def main() -> None:
    """Read one hook event and write the selected client's native decision."""
    arguments = sys.argv[1:]
    client = "codex" if not arguments else arguments[0]
    if len(arguments) > 1:
        client = ""
    # Bound before the read, because the deadline can expire while waiting for
    # standard input and the refusal below still has to pick a shape.
    raw = b""
    try:
        with _deadline():
            raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
            output = _output(raw, client)
        sys.stdout.buffer.write(output)
    except Exception:
        try:
            sys.stdout.buffer.write(_refusal_output(raw, client))
        except Exception:
            pass


if __name__ == "__main__":
    main()
