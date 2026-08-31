"""What one session decision is worth remembering, and nothing more.

PRD-06 turns these into a session summary and PRD-07 adds byte deltas for the
context diet, so the shape is fixed here and used by both. There is exactly one
rule about its contents: no field ever carries payload text. Entity names and
counts, yes; the value that produced them, never.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Prefix on the note of any event shim let through without looking at it —
#: too large to scan, or an analysis that failed. The summary keys on this,
#: because "shim saw nothing" and "shim did not look" are different sentences
#: and only one of them is reassuring.
NOT_INSPECTED = "not inspected"
MAX_DISPLAY_LABEL_CHARS = 120
UNKNOWN_TOOL_LABEL = "unknown tool"
UNSUPPORTED_EVENT_LABEL = "unsupported tool event"


def display_label(text: str, fallback: str) -> str:
    """Return one bounded terminal-safe label, or a fixed fallback."""
    if (
        not text.isprintable()
        or not text.strip()
        or len(text) > MAX_DISPLAY_LABEL_CHARS
    ):
        return fallback
    return text


@dataclass(frozen=True)
class Record:
    """One policy decision, with no payload content in any field."""

    client: str
    event: str
    tool_name: str
    direction: str
    mode: str
    action: str
    entities: tuple = ()
    #: Which file or URL the tool acted on, already scrubbed. Never a command:
    #: a shell string is the payload of an executable-text event, and the probe
    #: corpus has one holding a live credential.
    target: str = ""
    in_bytes: int = 0
    out_bytes: int = 0
    degraded_from: str = ""
    fields: int = 0
    #: Diet transforms applied (PRD-07 R5) and injection markers seen (R7).
    #: Markers only ever report: nothing here can cause a rewrite.
    transforms: tuple = ()
    markers: tuple = ()
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "client": self.client,
            "event": self.event,
            "tool_name": self.tool_name,
            "target": self.target,
            "direction": self.direction,
            "mode": self.mode,
            "action": self.action,
            "entities": {name: count for name, count in self.entities},
            "in_bytes": self.in_bytes,
            "out_bytes": self.out_bytes,
            "degraded_from": self.degraded_from,
            "fields": self.fields,
            "transforms": list(self.transforms),
            "markers": list(self.markers),
            "note": self.note,
        }


def _timestamp() -> str:
    import datetime

    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def remember(
    session_id: str, record: Record, latency_ms: int, ledger: bool = False
) -> None:
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
        from . import spool

        spool.append(session_id, entry)
    except Exception:
        pass
    if not ledger:
        return
    try:
        from . import ledger as store

        store.append(entry)
    except Exception:
        pass
