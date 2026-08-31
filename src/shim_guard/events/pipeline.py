"""One tool event: parse, classify, scan, decide, encode.

The detector stays pure and unaware of events. This module decides *what text*
to hand it and *what to do* with the answer, which is the whole difference
between a prompt scanner and a tool-level guard.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from shim_guard.policy import ALLOW, DENY, INBOUND, MASK, OBSERVE, decide, direction_for
from shim_guard.session.record import (
    NOT_INSPECTED,
    UNKNOWN_TOOL_LABEL,
    Record,
    display_label,
)

from .payload import PayloadTooLarge, inspect


@dataclass(frozen=True)
class Event:
    """One client-native event reduced to the facts shared policy uses."""

    __slots__ = ("tool", "payload", "target", "views_file")

    tool: str
    payload: object
    target: str
    views_file: bool


@dataclass(frozen=True)
class Adapter:
    """The client-owned facts the shared pipeline needs for one event."""

    __slots__ = ("client", "event", "root", "decode", "encode")

    client: str
    event: str
    root: str
    decode: Callable[[bytes], Event]
    encode: Callable[[str, object, str], bytes]


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


def _summary(counts) -> str:
    """Render entity counts without ever including a detected value."""
    return ", ".join(f"{entity} ({count})" for entity, count in counts)


MAX_TARGET_CHARS = 120
#: Bound on what the detector is asked to scan for a target. A path this long
#: is already pathological; the cost is bounded rather than the value trusted.
MAX_TARGET_SCAN_CHARS = 512


def _target(value: str, evaluate) -> str:
    """Return the scrubbed file or URL a tool acted on, or ``""``.

    A path can itself carry a secret, so it goes through the detector like any
    other text before it is remembered.

    Long values keep their *end*. The identifying part of a path is the file
    name, and a deep enough working directory pushes it past any left-hand
    truncation — which is how every file in one project came to be reported
    under the name of the directory above it.
    """
    if not value:
        return ""
    scanned = value[-MAX_TARGET_SCAN_CHARS:]
    decision = evaluate(scanned)
    text = _printable(decision.redacted_text if decision.counts else scanned)
    if len(text) <= MAX_TARGET_CHARS:
        return text
    return "…" + text[-MAX_TARGET_CHARS:]


def _printable(text: str) -> str:
    """Drop control characters from a target that will be displayed."""
    return "".join(character for character in text if character.isprintable())


def _size(value) -> int:
    """Return the byte size of a payload as the client would serialise it."""
    return len(json.dumps(value, ensure_ascii=False).encode())


def _message(direction: str, tool: str, counts: tuple, action: str) -> str:
    what = _summary(counts)
    where = f" in {tool}" if tool else ""
    if action == MASK:
        return f"shim: masked {what}{where}."
    if action == DENY:
        return f"shim: blocked {what}{where}."
    return f"shim: found {what}{where}. Not modified."


def process(
    entry: Adapter,
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
    without it every enabled entity is scanned, which is the default. The
    adapter owns validation and reduction of the native event shape.
    """
    event = entry.decode(raw)
    tool = event.tool

    direction = direction_for(entry.event, tool)
    mode = mode_for(direction, tool)
    target = _target(event.target, evaluate)
    tool_label = display_label(tool, UNKNOWN_TOOL_LABEL)
    if tool_label != UNKNOWN_TOOL_LABEL:
        decision = evaluate(tool_label)
        if decision.counts:
            tool_label = display_label(decision.redacted_text, UNKNOWN_TOOL_LABEL)
    if entities_for is not None:
        scoped = entities_for(tool, entry.event)
        original = evaluate

        def evaluate(text, _entities=scoped):  # noqa: F811 - scoped rebind
            return original(text, _entities)

    body = event.payload
    in_bytes = _size(body) if body else 0

    def record(
        action,
        counts=(),
        out_bytes=0,
        fields=0,
        note="",
        transforms=(),
        markers=(),
    ) -> Record:
        return Record(
            client=entry.client,
            event=entry.event,
            tool_name=tool_label,
            target=target,
            direction=direction,
            mode=mode,
            action=action,
            entities=counts,
            in_bytes=in_bytes,
            out_bytes=out_bytes,
            fields=fields,
            note=note,
            transforms=transforms,
            markers=markers,
        )

    if body is None:
        return Outcome(b"", record(ALLOW, note="no payload at this key"))

    # Diet and injection markers are inbound-only. Rewriting an outbound tool
    # argument to make it smaller changes what the model asked a tool to do,
    # and `observe` means look without touching, so neither applies there.
    # Diet additionally stops at anything that shows the model a file, because
    # the model has to be able to quote those bytes back to edit them.
    inbound = direction == INBOUND
    shrinkable = inbound and mode != OBSERVE and not event.views_file
    transforms = diet if shrinkable else ()
    try:
        result = inspect(body, evaluate, transforms, scan_markers=inbound)
    except PayloadTooLarge as error:
        # Over a bound the payload is not partially scanned; the event is
        # observed instead, and the reason is recorded rather than swallowed.
        return Outcome(b"", record(ALLOW, note=f"{NOT_INSPECTED}: {error}"))

    rewritten, findings, changed = result.value, result.findings, result.changed

    if not findings:
        if not changed:
            return Outcome(b"", record(ALLOW, fields=0, markers=result.markers))
        # Nothing sensitive, but the result got smaller. The policy action is
        # still `allow` — diet is not a decision about sensitive data — while
        # the wire needs the shape that carries a replacement payload.
        return Outcome(
            entry.encode(MASK, rewritten, ""),
            record(
                ALLOW,
                out_bytes=_size(rewritten),
                transforms=result.transforms,
                markers=result.markers,
            ),
        )

    counts = _counts(findings)
    action = decide(direction, mode)
    if action == ALLOW:
        return Outcome(b"", record(ALLOW, counts, fields=len(findings)))

    message = _message(direction, tool_label, counts, action)
    emitted = rewritten if action == MASK and changed else body
    output = entry.encode(action, emitted, message)
    out_bytes = _size(emitted) if action == MASK else in_bytes
    return Outcome(
        output,
        record(
            action,
            counts,
            out_bytes=out_bytes,
            fields=len(findings),
            transforms=result.transforms if action == MASK else (),
            markers=result.markers,
        ),
    )


__all__ = ["Adapter", "Event", "Outcome", "process"]
