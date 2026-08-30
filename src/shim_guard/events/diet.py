"""Shrink a tool result without changing what it says.

Every transform here is deterministic and idempotent, because the history sent
to the provider must be byte-identical on every request or prompt caching stops
hitting and the change costs money instead of saving it. A transform that
sometimes produces a different answer for the same input is worse than no
transform at all.

The harder rule is R4: never lose meaning. That is what decides the design of
each transform below, and what keeps two of the ones PRD-07 lists from shipping
at all — see `Q7.3` in the PRD.
"""

from __future__ import annotations

#: Transform names, stable because they are recorded and configurable.
JSON_COMPACTION = "json"
TRAILING_WHITESPACE = "whitespace"
TRANSFORMS = (JSON_COMPACTION, TRAILING_WHITESPACE)
DEFAULT_TRANSFORMS = TRANSFORMS

#: Below this a leaf is not worth examining; the win cannot pay for the risk.
MIN_CANDIDATE_CHARS = 64

_WHITESPACE = " \t\r\n"
_STRUCTURAL = "{}[]:,"


def compact_json(text: str) -> str:
    """Remove insignificant whitespace from JSON, or return ``text`` unchanged.

    This is a lexer, not a parse-and-re-emit. Round-tripping through
    ``json.loads``/``json.dumps`` would rewrite number literals
    (``1.10`` becomes ``1.1``, a long decimal loses digits to a float),
    collapse duplicate keys, and re-order nothing but re-render everything —
    all of which change what the model reads. Copying tokens verbatim and
    dropping only the whitespace between them cannot.

    Text that is not valid JSON comes back untouched, which is the common case
    and must stay cheap.
    """
    if not text or text[0] not in "{[":
        return text
    out = []
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character == '"':
            end = _string_end(text, index)
            if end is None:
                return text
            out.append(text[index:end])
            index = end
            continue
        if character in _WHITESPACE:
            index += 1
            continue
        if character not in _STRUCTURAL and not _is_literal_start(character):
            return text
        out.append(character)
        index += 1
    compacted = "".join(out)
    # A lexer cannot tell malformed JSON from valid JSON on its own, so the
    # result is only accepted when it parses to the same value as the source.
    if not _same_value(text, compacted):
        return text
    return compacted


def _is_literal_start(character: str) -> bool:
    return character.isalnum() or character in "+-."


def _string_end(text: str, start: int) -> int | None:
    """Return the index just past the JSON string beginning at ``start``."""
    index = start + 1
    length = len(text)
    while index < length:
        character = text[index]
        if character == "\\":
            index += 2
            continue
        if character == '"':
            return index + 1
        index += 1
    return None


def _same_value(original: str, compacted: str) -> bool:
    import json

    try:
        return json.loads(original) == json.loads(compacted)
    except (ValueError, RecursionError):
        return False


def strip_trailing_whitespace(text: str) -> str:
    """Remove trailing spaces from each line, keeping every line.

    Blank-line collapsing is deliberately absent. `Read` results arrive with
    line numbers prepended and the model edits by line number, so removing a
    blank line silently shifts every line after it. Trailing whitespace cannot
    change a line count.
    """
    if not any(character in text for character in " \t"):
        return text
    lines = text.split("\n")
    stripped = [line.rstrip(" \t") for line in lines]
    if stripped == lines:
        return text
    return "\n".join(stripped)


_APPLY = {
    JSON_COMPACTION: compact_json,
    TRAILING_WHITESPACE: strip_trailing_whitespace,
}


def shrink(text: str, enabled: tuple = DEFAULT_TRANSFORMS) -> tuple:
    """Return ``(text, applied)`` after every enabled transform that helped.

    A transform that does not make the text shorter is discarded rather than
    kept, so nothing is ever rewritten for no benefit.
    """
    if len(text) < MIN_CANDIDATE_CHARS:
        return text, ()
    applied = []
    current = text
    for name in TRANSFORMS:
        if name not in enabled:
            continue
        candidate = _APPLY[name](current)
        if len(candidate) < len(current):
            current = candidate
            applied.append(name)
    return current, tuple(applied)


__all__ = [
    "DEFAULT_TRANSFORMS",
    "JSON_COMPACTION",
    "MIN_CANDIDATE_CHARS",
    "TRAILING_WHITESPACE",
    "TRANSFORMS",
    "compact_json",
    "shrink",
    "strip_trailing_whitespace",
]
