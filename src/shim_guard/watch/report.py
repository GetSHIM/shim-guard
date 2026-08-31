"""What a watched session cost, said once, at the end.

Exact and approximate figures never share a column. The provider's token
counts are printed plain; anything shim inferred carries a `~`, and the
section split says so in words as well. The distinction matters because the two
have completely different standing: one is what was billed, the other is a
guess at how it divided.
"""

from __future__ import annotations

from .measure import OTHER, SECTIONS, Usage

#: Dollars per million tokens, by model prefix, taken from Anthropic's public
#: pricing page on 30 Aug 2026. Order matters: the first prefix that matches
#: wins, so longer names come first.
#:
#: A price table in a local tool goes stale and cannot be checked offline, and
#: a stale dollar figure is worse than none — people quote it. So an unknown
#: model prints no spend line at all rather than a number derived from a guess,
#: and every figure this produces is marked approximate.
PRICES = (
    ("claude-opus-4", (15.0, 75.0, 18.75, 1.5)),
    ("claude-opus-5", (15.0, 75.0, 18.75, 1.5)),
    ("claude-sonnet-4", (3.0, 15.0, 3.75, 0.3)),
    ("claude-sonnet-5", (3.0, 15.0, 3.75, 0.3)),
    ("claude-haiku-4", (1.0, 5.0, 1.25, 0.1)),
    ("claude-3-5-haiku", (0.8, 4.0, 1.0, 0.08)),
)
PRICED_ON = "2026-08-30"
_PER = 1_000_000


def _price(model: str):
    for prefix, rates in PRICES:
        if model.startswith(prefix):
            return rates
    return None


def spend(exchanges: list) -> tuple:
    """Return (dollars, priced, unpriced) across every exchange.

    Cache reads and cache writes are charged at their own rates, which is the
    whole reason this is not `total_input * one_rate`: on a warm session the
    cached share is the overwhelming majority of the tokens and a tenth of the
    price.
    """
    total = 0.0
    priced = 0
    unpriced = set()
    for exchange in exchanges:
        rates = _price(exchange.model or "")
        if rates is None:
            if exchange.model:
                unpriced.add(exchange.model)
            continue
        fresh, output, write, read = rates
        usage = exchange.usage
        total += (
            usage.input_tokens * fresh
            + usage.output_tokens * output
            + usage.cache_creation_input_tokens * write
            + usage.cache_read_input_tokens * read
        ) / _PER
        priced += 1
    return total, priced, sorted(unpriced)


def totals(exchanges: list) -> Usage:
    combined = Usage()
    for exchange in exchanges:
        usage = exchange.usage
        combined = Usage(
            input_tokens=combined.input_tokens + usage.input_tokens,
            output_tokens=combined.output_tokens + usage.output_tokens,
            cache_creation_input_tokens=(
                combined.cache_creation_input_tokens + usage.cache_creation_input_tokens
            ),
            cache_read_input_tokens=(
                combined.cache_read_input_tokens + usage.cache_read_input_tokens
            ),
        )
    return combined


def section_totals(exchanges: list) -> dict:
    """Return approximate input tokens per section, summed over the session."""
    combined: dict = {}
    for exchange in exchanges:
        for name, tokens in exchange.tokens_by_section().items():
            combined[name] = combined.get(name, 0) + tokens
    return combined


def entity_totals(exchanges: list) -> dict:
    combined: dict = {}
    for exchange in exchanges:
        for entity, count in exchange.entities.items():
            combined[entity] = combined.get(entity, 0) + count
    return combined


def at_file_totals(exchanges: list) -> tuple:
    return (
        sum(exchange.at_files.count for exchange in exchanges),
        sum(exchange.at_files.bytes for exchange in exchanges),
    )


def _duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60:02d}s"


def _thousands(value: int) -> str:
    return f"{value:,}"


def _order(names) -> list:
    """Named sections first, in the order a reader expects, then the rest."""
    known = [name for name in SECTIONS if name in names]
    rest = sorted(name for name in names if name not in SECTIONS and name != OTHER)
    return known + rest + ([OTHER] if OTHER in names else [])


def render(session, seconds: float) -> str:
    """Return the end-of-session summary, or ``""`` when nothing was seen."""
    exchanges = [
        exchange for exchange in session.exchanges if exchange.path.endswith("messages")
    ]
    if not exchanges and not session.errors:
        return ""
    lines = [f"shim watch — {_duration(seconds)}, {len(exchanges)} requests"]

    combined = totals(exchanges)
    if combined.total_input:
        lines.append(f"  input     {_thousands(combined.total_input)} tokens  (exact)")
        cached = combined.cache_read_input_tokens
        if cached:
            share = round(100 * cached / combined.total_input)
            lines.append(f"    cache read   {_thousands(cached)}   {share}%")
        if combined.cache_creation_input_tokens:
            lines.append(
                f"    cache write  {_thousands(combined.cache_creation_input_tokens)}"
            )
    if combined.output_tokens:
        lines.append(
            f"  output    {_thousands(combined.output_tokens)} tokens  (exact)"
        )

    by_section = section_totals(exchanges)
    if by_section:
        total = sum(by_section.values())
        lines.append("  where the input went  (approximate — split by byte share)")
        for name in _order(by_section):
            tokens = by_section[name]
            share = round(100 * tokens / total) if total else 0
            lines.append(f"    {name:<9} ~{_thousands(tokens):>12}  {share:>3}%")

    count, size = at_file_totals(exchanges)
    if count:
        lines.append(
            f"  @ files   {count} inlined, {_thousands(size)} bytes "
            f"(invisible to hooks)"
        )

    found = entity_totals(exchanges)
    if found:
        listed = ", ".join(
            f"{count} {entity}"
            for entity, count in sorted(found.items(), key=lambda p: (-p[1], p[0]))
        )
        lines.append(f"  found     {listed} in traffic")

    dollars, priced, unpriced = spend(exchanges)
    if priced:
        lines.append(f"  spend     ~${dollars:,.2f}  (approximate, {PRICED_ON} prices)")
    if unpriced:
        lines.append(f"  spend     not priced for {', '.join(unpriced)}")

    biggest = max(exchanges, key=lambda e: e.request_bytes, default=None)
    if biggest is not None and biggest.request_bytes:
        largest = max(biggest.sections.items(), key=lambda p: p[1], default=("", 0))
        if largest[1]:
            lines.append(
                f"  largest   one request was {_thousands(biggest.request_bytes)} "
                f"bytes, {largest[0]} {round(100 * largest[1] / biggest.request_bytes)}% of it"
            )

    if session.errors:
        lines.append(f"  errors    {session.errors} request(s) could not be forwarded")
    lines.append("  nothing was modified, and no request body was written to disk")
    return "\n".join(lines)


def as_json(session, seconds: float) -> dict:
    """The same numbers as data. Exactness is marked, not implied."""
    exchanges = [
        exchange for exchange in session.exchanges if exchange.path.endswith("messages")
    ]
    combined = totals(exchanges)
    dollars, priced, unpriced = spend(exchanges)
    count, size = at_file_totals(exchanges)
    return {
        "seconds": round(seconds, 1),
        "requests": len(exchanges),
        "errors": session.errors,
        "exact": {
            "input_tokens": combined.total_input,
            "output_tokens": combined.output_tokens,
            "cache_read_input_tokens": combined.cache_read_input_tokens,
            "cache_creation_input_tokens": combined.cache_creation_input_tokens,
            "uncached_input_tokens": combined.input_tokens,
        },
        "approximate": {
            "tokens_by_section": section_totals(exchanges),
            "spend_usd": round(dollars, 4) if priced else None,
            "priced_on": PRICED_ON if priced else None,
            "unpriced_models": unpriced,
        },
        "at_files": {"count": count, "bytes": size},
        "entities": entity_totals(exchanges),
    }


__all__ = [
    "PRICED_ON",
    "PRICES",
    "as_json",
    "at_file_totals",
    "entity_totals",
    "render",
    "section_totals",
    "spend",
    "totals",
]
