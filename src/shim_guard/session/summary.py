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


def _acted(records: list) -> list:
    """Return only the records where shim did something worth reporting."""
    return [record for record in records if record.get("action") not in (None, "allow")]


def render(records: list, capped: bool = False) -> str:
    """Return the session summary, or ``""`` when there is nothing to say."""
    acted = _acted(records)
    if not acted:
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
            sources = _sources(relevant)
            shown = ", ".join(sources[:MAX_SOURCES])
            if len(sources) > MAX_SOURCES:
                shown += f", +{len(sources) - MAX_SOURCES} more"
            column = label if first else " " * len(label)
            first = False
            where = f"  ({shown})" if shown else ""
            lines.append(f"  {column:<9} {count} {entity}{where}")
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
    return {
        "events": len(records),
        "acted": len(acted),
        "actions": actions,
        "overhead_ms": {"median": median, "p95": p95},
        "capped": capped,
    }


__all__ = ["as_json", "render"]
