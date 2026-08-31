"""What a request contained and what it cost, derived without keeping any of it.

Everything here is pure and takes its input as a parsed document or a slice of
response text, so it can be tested without a socket and cannot be the reason a
request fails. The proxy calls it inside a guard: a measurement that raises is
dropped, never forwarded as an error.

Two numbers with very different standing come out of this module and PRD-09 R4
requires them to stay visually distinguishable everywhere:

* **Exact** — the provider's own `usage` block, read off the wire verbatim.
* **Approximate** — how that exact total divides across `tools`, `system` and
  `messages`. Section attribution has no ground truth on the wire, so it is
  derived by byte share.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

#: Top-level request keys worth naming in a report. Everything else in a real
#: Claude Code request (`model`, `max_tokens`, `thinking`, `metadata`,
#: `output_config`, `context_management`, `stream`) measured under 250 bytes
#: against a live session, against 139 KB for `tools` — so they are summed into
#: `other` rather than each given a line nobody reads.
SECTIONS = ("tools", "system", "messages")
OTHER = "other"

#: Past this a request is counted but not broken down. A body this size is
#: already pathological, and the point is to bound the work rather than to
#: trust the input.
MAX_BODY_BYTES = 8_000_000

#: A provider-controlled model name reaches terminal and JSON reports.
MAX_MODEL_CHARS = 120
UNKNOWN_MODEL = "unknown"

#: How Claude Code delivers a file referenced with `@`. It never reaches a
#: hook: the client resolves it while building the prompt and inlines it as a
#: synthetic tool result. Captured verbatim from a live session — the wrapper
#: is a `<system-reminder>` holding this sentence and a `file_path`.
AT_FILE_MARKER = "Called the Read tool with the following input:"
_REMINDER_OPEN = "<system-reminder>"
_REMINDER_CLOSE = "</system-reminder>"


@dataclass(frozen=True)
class Usage:
    """The provider's own token counts. Every field is exact or absent."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total_input(self) -> int:
        """Every token the provider charged as input, cached or not.

        `input_tokens` alone is misleading: a warm session reports 2 there and
        91,562 under `cache_read_input_tokens`.
        """
        return (
            self.input_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    def merge(self, other: Usage) -> Usage:
        """Combine the counts a single response reports in stages.

        `message_start` carries the input side and an opening output count;
        `message_delta` carries the final output count. The later output count
        replaces rather than adds, which is why this is not a sum.
        """
        return Usage(
            input_tokens=self.input_tokens or other.input_tokens,
            output_tokens=max(self.output_tokens, other.output_tokens),
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens or other.cache_creation_input_tokens
            ),
            cache_read_input_tokens=(
                self.cache_read_input_tokens or other.cache_read_input_tokens
            ),
        )


def _int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def usage_from(document: object) -> Usage:
    """Return the usage carried by one decoded SSE event, or an empty one."""
    if not isinstance(document, dict):
        return Usage()
    block = document.get("usage")
    if not isinstance(block, dict):
        message = document.get("message")
        block = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(block, dict):
        return Usage()
    return Usage(
        input_tokens=_int(block.get("input_tokens")),
        output_tokens=_int(block.get("output_tokens")),
        cache_creation_input_tokens=_int(block.get("cache_creation_input_tokens")),
        cache_read_input_tokens=_int(block.get("cache_read_input_tokens")),
    )


class UsageReader:
    """Pull token counts out of a server-sent-event stream as it goes past.

    The stream is forwarded to the client byte for byte; this only ever sees a
    copy. It holds at most one partial event, so a response of any length costs
    the same memory — PRD-09 R3 forbids buffering the body and R5 forbids
    keeping it.
    """

    #: One SSE event that never completes must not grow without bound.
    MAX_PENDING = 1_000_000

    def __init__(self) -> None:
        self.usage = Usage()
        self._pending = ""

    def feed(self, text: str) -> None:
        """Consume a decoded slice of the response."""
        self._pending += text
        while "\n\n" in self._pending:
            event, self._pending = self._pending.split("\n\n", 1)
            self._consume(event)
        if len(self._pending) > self.MAX_PENDING:
            self._pending = ""

    def _consume(self, event: str) -> None:
        for line in event.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                document = json.loads(line[5:].strip())
            except ValueError:
                continue
            self.usage = self.usage.merge(usage_from(document))


def _size(value: object) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False).encode())
    except (TypeError, ValueError):
        return 0


def sections(document: object) -> dict:
    """Return the byte size of each named request section, plus `other`."""
    if not isinstance(document, dict):
        return {}
    found = {name: _size(document[name]) for name in SECTIONS if name in document}
    rest = sum(_size(value) for name, value in document.items() if name not in SECTIONS)
    if rest:
        found[OTHER] = rest
    return found


def _texts(document: dict):
    """Yield every model-visible string in the message history."""
    for message in document.get("messages") or ():
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            yield content
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    yield part["text"]


@dataclass(frozen=True)
class AtFiles:
    """Files the client inlined itself, which no hook ever saw."""

    count: int = 0
    bytes: int = 0


def at_files(document: object) -> AtFiles:
    """Return how much of the prompt is `@`-referenced file content.

    This is the coverage gap `shim watch` exists for: the client reads these
    files and inlines them while building the prompt, so no `PreToolUse` fires
    and the hook never learns they were read.
    """
    if not isinstance(document, dict):
        return AtFiles()
    count = 0
    total = 0
    for text in _texts(document):
        start = 0
        while True:
            start = text.find(_REMINDER_OPEN, start)
            if start < 0:
                break
            end = text.find(_REMINDER_CLOSE, start)
            if end < 0:
                break
            block = text[start : end + len(_REMINDER_CLOSE)]
            if AT_FILE_MARKER in block:
                count += 1
                total += len(block.encode())
            start = end + len(_REMINDER_CLOSE)
    return AtFiles(count, total)


def attribute(by_bytes: dict, exact_total: int) -> dict:
    """Split an exact token total across sections by their byte share.

    The total is the provider's own number and is preserved to the token; only
    the split between sections is inferred. Sections do not tokenise at the
    same density — a JSON tool schema packs worse than prose — so this is
    labelled approximate wherever it is shown, and never presented as if the
    provider had reported it.

    The remainder goes to the largest section rather than being dropped, so the
    parts always add up to the whole.
    """
    total_bytes = sum(by_bytes.values())
    if not total_bytes or exact_total <= 0:
        return {}
    shares = {
        name: exact_total * size // total_bytes for name, size in by_bytes.items()
    }
    remainder = exact_total - sum(shares.values())
    if remainder:
        largest = max(by_bytes, key=lambda name: (by_bytes[name], name))
        shares[largest] += remainder
    return shares


@dataclass
class Exchange:
    """One request and its response, reduced to numbers before it is kept.

    No field here can hold traffic. `entities` is a count per type and
    `sections` a size per name; the bodies they were derived from are gone by
    the time this exists, which is what PRD-09 R5 requires.
    """

    path: str = ""
    model: str = ""
    status: int = 0
    request_bytes: int = 0
    usage: Usage = field(default_factory=Usage)
    sections: dict = field(default_factory=dict)
    entities: dict = field(default_factory=dict)
    at_files: AtFiles = field(default_factory=AtFiles)
    measured: bool = True

    def tokens_by_section(self) -> dict:
        return attribute(self.sections, self.usage.total_input)


def inspect_request(body: bytes, evaluate=None) -> Exchange:
    """Measure one outgoing request body without retaining any of it."""
    exchange = Exchange(request_bytes=len(body))
    if len(body) > MAX_BODY_BYTES:
        exchange.measured = False
        return exchange
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        exchange.measured = False
        return exchange
    if isinstance(document, dict) and isinstance(document.get("model"), str):
        model = document["model"]
        exchange.model = (
            model
            if model and len(model) <= MAX_MODEL_CHARS and model.isprintable()
            else UNKNOWN_MODEL
        )
    exchange.sections = sections(document)
    exchange.at_files = at_files(document)
    if evaluate is not None and isinstance(document, dict):
        counts: dict = {}
        for text in _texts(document):
            decision = evaluate(text)
            for entity, count in getattr(decision, "counts", ()):
                counts[entity] = counts.get(entity, 0) + count
        exchange.entities = counts
    return exchange


__all__ = [
    "AT_FILE_MARKER",
    "AtFiles",
    "Exchange",
    "MAX_BODY_BYTES",
    "MAX_MODEL_CHARS",
    "OTHER",
    "SECTIONS",
    "Usage",
    "UsageReader",
    "UNKNOWN_MODEL",
    "at_files",
    "attribute",
    "inspect_request",
    "sections",
    "usage_from",
]
