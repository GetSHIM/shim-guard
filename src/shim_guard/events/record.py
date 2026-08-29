"""What one decision is worth remembering, and nothing more.

PRD-06 turns these into a session summary and PRD-07 adds byte deltas for the
context diet, so the shape is fixed here and used by both. There is exactly one
rule about its contents: no field ever carries payload text. Entity names and
counts, yes; the value that produced them, never.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
    note: str = field(default="")

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
            "note": self.note,
        }
