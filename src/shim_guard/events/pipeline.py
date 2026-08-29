"""One tool event: parse, classify, scan, decide, encode.

The detector stays pure and unaware of events. This module decides *what text*
to hand it and *what to do* with the answer, which is the whole difference
between a prompt scanner and a tool-level guard.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .adapters import summary
from .payload import PayloadTooLarge, mask
from .policy import ALLOW, DENY, MASK, decide, direction_for
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


def _message(direction: str, tool: str, counts: tuple, action: str) -> str:
    what = summary(counts)
    where = f" in {tool}" if tool else ""
    if action == MASK:
        return f"shim: masked {what}{where}."
    if action == DENY:
        return f"shim: blocked {what}{where}."
    return f"shim: found {what}{where}. Not modified."


def process(client: str, raw: bytes, mode_for, evaluate) -> Outcome:
    """Handle one tool event and return its native response.

    ``mode_for(direction, tool)`` supplies the configured mode, so policy stays
    injectable and this function stays pure.
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
    body = document.get(entry.root)
    in_bytes = len(json.dumps(body, ensure_ascii=False).encode()) if body else 0

    def record(action, decision, counts=(), out_bytes=0, fields=0, note="") -> Record:
        return Record(
            client=client,
            event=event,
            tool_name=tool,
            direction=direction,
            mode=mode,
            action=action,
            entities=counts,
            in_bytes=in_bytes,
            out_bytes=out_bytes,
            degraded_from=decision.degraded_from if decision else "",
            fields=fields,
            note=note,
        )

    if body is None:
        return Outcome(b"", record(ALLOW, None, note="no payload at this key"))

    try:
        rewritten, findings, changed = mask(body, evaluate)
    except PayloadTooLarge as error:
        # Over a bound the payload is not partially scanned; the event is
        # observed instead, and the reason is recorded rather than swallowed.
        return Outcome(b"", record(ALLOW, None, note=str(error)))

    if not findings:
        return Outcome(b"", record(ALLOW, None, fields=0))

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
    out_bytes = (
        len(json.dumps(emitted, ensure_ascii=False).encode())
        if decision.action == MASK
        else in_bytes
    )
    return Outcome(
        output,
        record(
            decision.action, decision, counts, out_bytes=out_bytes, fields=len(findings)
        ),
    )


__all__ = ["Outcome", "process"]
