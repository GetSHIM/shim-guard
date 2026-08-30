"""One tool event: parse, classify, scan, decide, encode.

The detector stays pure and unaware of events. This module decides *what text*
to hand it and *what to do* with the answer, which is the whole difference
between a prompt scanner and a tool-level guard.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .adapters import summary
from .payload import PayloadTooLarge, inspect
from .policy import ALLOW, DENY, INBOUND, MASK, OBSERVE, decide, direction_for
from .record import Record
from .registry import TOOL_KEY, adapter


@dataclass(frozen=True)
class Outcome:
    """The bytes to write, and the record to remember."""

    __slots__ = ("output", "record")

    output: bytes
    record: Record


def _counts(findings) -> tuple:
    totals: dict = {}
    for _path, decision in findings:
        for entity, count in decision.counts:
            totals[entity] = totals.get(entity, 0) + count
    return tuple(sorted(totals.items()))


#: Input keys naming *what* a tool acted on. `command` is deliberately absent:
#: a shell string is the payload of an executable-text event, not a target, and
#: the probe corpus contains one holding a live credential.
TARGET_KEYS = ("file_path", "notebook_path", "path", "url")
#: The subset of those that name a file on the user's own disk. A result that
#: is a *view of a file* has to reach the model byte for byte, because the model
#: reproduces those bytes to edit it: `Edit` matches `old_string` against what
#: is on disk, not against what it was shown. Compacting a pretty-printed
#: `settings.json` on the way in makes the next `Edit` miss — observed against
#: Claude Code 2.1.251, which then spent three `Bash` calls working out why. A
#: `url` is not in this set: nothing edits a fetched page by byte match.
FILE_VIEW_KEYS = ("file_path", "notebook_path", "path")
MAX_TARGET_CHARS = 120
#: Bound on what the detector is asked to scan for a target. A path this long
#: is already pathological; the cost is bounded rather than the value trusted.
MAX_TARGET_SCAN_CHARS = 512


def _target(document: dict, evaluate) -> str:
    """Return the scrubbed file or URL a tool acted on, or ``""``.

    A path can itself carry a secret, so it goes through the detector like any
    other text before it is remembered.

    Long values keep their *end*. The identifying part of a path is the file
    name, and a deep enough working directory pushes it past any left-hand
    truncation — which is how every file in one project came to be reported
    under the name of the directory above it.
    """
    body = document.get("tool_input")
    if not isinstance(body, dict):
        return ""
    for key in TARGET_KEYS:
        value = body.get(key)
        if not isinstance(value, str) or not value:
            continue
        scanned = value[-MAX_TARGET_SCAN_CHARS:]
        decision = evaluate(scanned)
        text = _printable(decision.redacted_text if decision.counts else scanned)
        if len(text) <= MAX_TARGET_CHARS:
            return text
        return "…" + text[-MAX_TARGET_CHARS:]
    return ""


def _views_a_file(document: dict) -> bool:
    """Return whether this event's result is a view of a file on disk."""
    body = document.get("tool_input")
    if not isinstance(body, dict):
        return False
    return any(isinstance(body.get(key), str) and body[key] for key in FILE_VIEW_KEYS)


def _printable(text: str) -> str:
    """Drop control characters from a value that will be displayed.

    A file name is attacker-controllable — checking out a repository is enough
    — and this one is repeated into the session summary, which the client
    renders in the user's terminal. An escape sequence surviving that far would
    let a file name repaint the screen.
    """
    return "".join(character for character in text if character.isprintable())


def _size(value) -> int:
    """Return the byte size of a payload as the client would serialise it."""
    return len(json.dumps(value, ensure_ascii=False).encode())


def _message(direction: str, tool: str, counts: tuple, action: str) -> str:
    what = summary(counts)
    where = f" in {tool}" if tool else ""
    if action == MASK:
        return f"shim: masked {what}{where}."
    if action == DENY:
        return f"shim: blocked {what}{where}."
    return f"shim: found {what}{where}. Not modified."


def process(
    client: str,
    raw: bytes,
    mode_for,
    evaluate,
    diet: tuple = (),
    entities_for=None,
) -> Outcome:
    """Handle one tool event and return its native response.

    ``mode_for(direction, tool)`` supplies the configured mode and ``diet`` the
    enabled transforms, so policy stays injectable and this function stays pure.
    ``entities_for(tool, event)`` narrows what is looked for on this one tool;
    without it every enabled entity is scanned, which is the default.
    """
    document = json.loads(raw.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError("hook payload must be an object")
    event = document.get("hook_event_name")
    if not isinstance(event, str):
        raise ValueError("hook payload has no event name")
    tool = document.get(TOOL_KEY) or ""
    if not isinstance(tool, str):
        raise ValueError("tool name must be text")

    entry = adapter(client, event)
    direction = direction_for(event, tool)
    mode = mode_for(direction, tool)
    if entities_for is not None:
        scoped = entities_for(tool, event)
        original = evaluate

        def evaluate(text, _entities=scoped):  # noqa: F811 - scoped rebind
            return original(text, _entities)

    body = document.get(entry.root)
    in_bytes = _size(body) if body else 0

    target = _target(document, evaluate)

    def record(
        action,
        decision,
        counts=(),
        out_bytes=0,
        fields=0,
        note="",
        transforms=(),
        markers=(),
    ) -> Record:
        return Record(
            client=client,
            event=event,
            tool_name=tool,
            target=target,
            direction=direction,
            mode=mode,
            action=action,
            entities=counts,
            in_bytes=in_bytes,
            out_bytes=out_bytes,
            degraded_from=decision.degraded_from if decision else "",
            fields=fields,
            note=note,
            transforms=transforms,
            markers=markers,
        )

    if body is None:
        return Outcome(b"", record(ALLOW, None, note="no payload at this key"))

    # Diet and injection markers are inbound-only. Rewriting an outbound tool
    # argument to make it smaller changes what the model asked a tool to do,
    # and `observe` means look without touching, so neither applies there.
    # Diet additionally stops at anything that shows the model a file, because
    # the model has to be able to quote those bytes back to edit them.
    inbound = direction == INBOUND and entry.capabilities.can_rewrite
    shrinkable = inbound and mode != OBSERVE and not _views_a_file(document)
    transforms = diet if shrinkable else ()
    try:
        result = inspect(body, evaluate, transforms, scan_markers=inbound)
    except PayloadTooLarge as error:
        # Over a bound the payload is not partially scanned; the event is
        # observed instead, and the reason is recorded rather than swallowed.
        return Outcome(b"", record(ALLOW, None, note=str(error)))

    rewritten, findings, changed = result.value, result.findings, result.changed

    if not findings:
        if not changed:
            return Outcome(b"", record(ALLOW, None, fields=0, markers=result.markers))
        # Nothing sensitive, but the result got smaller. The policy action is
        # still `allow` — diet is not a decision about sensitive data — while
        # the wire needs the shape that carries a replacement payload.
        return Outcome(
            entry.encode(MASK, rewritten, ""),
            record(
                ALLOW,
                None,
                out_bytes=_size(rewritten),
                transforms=result.transforms,
                markers=result.markers,
            ),
        )

    counts = _counts(findings)
    decision = decide(
        direction,
        mode,
        can_rewrite=entry.capabilities.can_rewrite,
        can_report=entry.capabilities.can_report,
    )
    if decision.action == ALLOW:
        return Outcome(b"", record(ALLOW, decision, counts, fields=len(findings)))

    message = _message(direction, tool, counts, decision.action)
    emitted = rewritten if decision.action == MASK and changed else body
    output = entry.encode(decision.action, emitted, message)
    out_bytes = _size(emitted) if decision.action == MASK else in_bytes
    return Outcome(
        output,
        record(
            decision.action,
            decision,
            counts,
            out_bytes=out_bytes,
            fields=len(findings),
            transforms=result.transforms if decision.action == MASK else (),
            markers=result.markers,
        ),
    )


__all__ = ["Outcome", "process"]
