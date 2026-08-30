"""Turn a session's records into the one thing the user reads.

When shim works, it says nothing, and a tool that is invisible when it succeeds
gets uninstalled in week two. This is the whole of the product's visible
output, so it is worth being exact about: it names tools and files, counts
entities, and never shows a value.
"""

from __future__ import annotations

ACTION_LABELS = (
    ("mask", "masked"),
    ("deny", "blocked"),
    ("report", "warned"),
)
#: How many distinct places are named per line before the rest are counted.
MAX_SOURCES = 3
#: Bytes per token. A rough industry average for English and code, used only
#: because no provider `usage` block is visible from a hook. Every figure it
#: produces is labelled approximate; `shim watch` will replace it with real
#: counts rather than a better guess.
BYTES_PER_TOKEN = 4


def _sources(records: list) -> list:
    """Return the tools involved, most active first, without duplicates."""
    order: dict = {}
    for record in records:
        tool = record.get("tool_name") or ""
        target = record.get("target") or ""
        if tool:
            where = f"{tool} {_basename(target)}" if target else tool
        else:
            where = _event_label(record.get("event", ""))
        if where:
            order[where] = order.get(where, 0) + 1
    return [name for name, _count in sorted(order.items(), key=lambda p: (-p[1], p[0]))]


def _basename(target: str) -> str:
    """Return the last path or URL segment, which is what identifies a file."""
    if "://" in target:
        return target
    return target.rsplit("/", 1)[-1] or target


def _event_label(event: str) -> str:
    if event in ("UserPromptSubmit", "userPromptTransformed"):
        return "your prompt"
    return event


def _totals(records: list) -> list:
    counts: dict = {}
    for record in records:
        entities = record.get("entities")
        if not isinstance(entities, dict):
            continue
        for entity, count in entities.items():
            if isinstance(entity, str) and isinstance(count, int):
                counts[entity] = counts.get(entity, 0) + count
    return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))


def _overhead(records: list) -> tuple:
    latencies = sorted(
        record["latency_ms"]
        for record in records
        if isinstance(record.get("latency_ms"), (int, float))
    )
    if not latencies:
        return (0, 0)
    median = latencies[len(latencies) // 2]
    index = min(len(latencies) - 1, int(round(0.95 * (len(latencies) - 1))))
    return (round(median), round(latencies[index]))


def _saved(records: list) -> int:
    """Return bytes removed from tool results by the diet transforms."""
    total = 0
    for record in records:
        if not record.get("transforms"):
            continue
        before = record.get("in_bytes")
        after = record.get("out_bytes")
        if isinstance(before, int) and isinstance(after, int) and before > after:
            total += before - after
    return total


def _marker_totals(records: list) -> list:
    counts: dict = {}
    for record in records:
        markers = record.get("markers")
        if not isinstance(markers, list):
            continue
        for marker in markers:
            if isinstance(marker, str):
                counts[marker] = counts.get(marker, 0) + 1
    return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))


def _where(records: list) -> str:
    """Return the parenthesised list of places, or ``""``."""
    sources = _sources(records)
    if not sources:
        return ""
    shown = ", ".join(sources[:MAX_SOURCES])
    if len(sources) > MAX_SOURCES:
        shown += f", +{len(sources) - MAX_SOURCES} more"
    return f"  ({shown})"


def _carrying(records: list, marker: str) -> list:
    return [
        record
        for record in records
        if isinstance(record.get("markers"), list) and marker in record["markers"]
    ]


def _uninspected(records: list) -> list:
    """Return events shim let through without looking at them."""
    from shim_guard.events.record import NOT_INSPECTED

    return [
        record
        for record in records
        if isinstance(record.get("note"), str)
        and record["note"].startswith(NOT_INSPECTED)
    ]


def _acted(records: list) -> list:
    """Return only the records where shim did something worth reporting."""
    return [record for record in records if record.get("action") not in (None, "allow")]


def render(records: list, capped: bool = False) -> str:
    """Return the session summary, or ``""`` when there is nothing to say."""
    acted = _acted(records)
    saved = _saved(records)
    markers = _marker_totals(records)
    skipped = _uninspected(records)
    if not acted and not saved and not markers and not skipped:
        return ""
    lines: list = []
    for action, label in ACTION_LABELS:
        matching = [record for record in acted if record.get("action") == action]
        if not matching:
            continue
        first = True
        for entity, count in _totals(matching):
            relevant = [
                record
                for record in matching
                if isinstance(record.get("entities"), dict)
                and entity in record["entities"]
            ]
            column = label if first else " " * len(label)
            first = False
            lines.append(f"  {column:<9} {count} {entity}{_where(relevant)}")
    first = True
    for marker, count in markers:
        # "Something told your agent to ignore its instructions" is only
        # actionable with the file name attached, so markers are sourced
        # exactly like entities.
        column = "flagged" if first else " " * len("flagged")
        first = False
        lines.append(
            f"  {column:<9} {count} {marker}{_where(_carrying(records, marker))}"
        )
    if skipped:
        # The one line here that is not good news. Saying nothing would let a
        # clean-looking summary stand for a payload shim never examined.
        lines.append(
            f"  {'skipped':<9} {len(skipped)} not inspected, passed through"
            f"{_where(skipped)}"
        )
    if saved:
        tokens = saved // BYTES_PER_TOKEN
        lines.append(
            f"  {'shrank':<9} {saved} bytes of tool results (~{tokens} tokens)"
        )
    if not lines:
        # Records claiming an action but carrying no readable entity counts. A
        # heading over an empty list reads as "something happened but we will
        # not say what", which is worse than saying nothing at all.
        return ""
    median, p95 = _overhead(records)
    lines.append(f"  {'overhead':<9} {median} ms median, {p95} ms p95")
    if capped:
        lines.append(
            "  (session log reached its size cap; later events are not counted)"
        )
    return "\n".join(["shim — this session", *lines])


def as_json(records: list, capped: bool = False) -> dict:
    """Return the same summary as data, for `shim report --json`."""
    acted = _acted(records)
    median, p95 = _overhead(records)
    actions: dict = {}
    for action, _label in ACTION_LABELS:
        matching = [record for record in acted if record.get("action") == action]
        if matching:
            actions[action] = {
                "entities": dict(_totals(matching)),
                "sources": _sources(matching),
                "events": len(matching),
            }
    saved = _saved(records)
    return {
        "events": len(records),
        "acted": len(acted),
        "actions": actions,
        "overhead_ms": {"median": median, "p95": p95},
        "bytes_saved": saved,
        "tokens_saved_approx": saved // BYTES_PER_TOKEN,
        "not_inspected": len(_uninspected(records)),
        "markers": {
            marker: {"count": count, "sources": _sources(_carrying(records, marker))}
            for marker, count in _marker_totals(records)
        },
        "capped": capped,
    }


__all__ = ["as_json", "render"]
