"""Walk a tool payload, mask its string leaves, and re-emit the same shape.

Tool inputs and results are nested JSON, not flat strings, and the clients are
explicit that a replacement "must match the tool's output shape". So the
traversal never stringifies an object to scan it and never changes a type: a
string leaf is replaced by a string, and every list, dict and scalar around it
comes back as it went in.

Two bounds matter more here than on the prompt path. A single `Read` result can
carry tens of thousands of characters, and a deeply nested MCP result could
recurse without limit, so both are capped. Over the cap the caller is told to
fall back to observing rather than being handed a partial scan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

MAX_TEXT_CHARACTERS = 200_000
MAX_DEPTH = 24
MAX_LEAVES = 2_000

Path = tuple  # tuple[str | int, ...]


class PayloadTooLarge(ValueError):
    """The payload is past a bound, so it must not be partially scanned."""


@dataclass
class Traversal:
    """String leaves found in one payload, in deterministic document order."""

    leaves: list = field(default_factory=list)
    characters: int = 0

    def add(self, path: Path, text: str) -> None:
        self.leaves.append((path, text))
        self.characters += len(text)
        if len(self.leaves) > MAX_LEAVES:
            raise PayloadTooLarge("payload has too many text fields to scan safely")
        if self.characters > MAX_TEXT_CHARACTERS:
            raise PayloadTooLarge("payload text exceeds the safe analysis limit")


def walk(value: Any, root: Path = ()) -> Traversal:
    """Return every string leaf under ``value`` with its path."""
    found = Traversal()
    _walk(value, root, found, 0)
    return found


def _walk(value: Any, path: Path, found: Traversal, depth: int) -> None:
    if depth > MAX_DEPTH:
        raise PayloadTooLarge("payload is nested more deeply than is safe to scan")
    if isinstance(value, str):
        if value:
            found.add(path, value)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                _walk(item, path + (key,), found, depth + 1)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _walk(item, path + (index,), found, depth + 1)


def read_at(value: Any, path: Path) -> Any:
    """Return the value at ``path``, raising KeyError/IndexError if absent."""
    for key in path:
        value = value[key]
    return value


def replace(value: Any, replacements: dict) -> Any:
    """Return a copy of ``value`` with the given paths replaced.

    The copy is structural: containers are rebuilt, scalars are shared, and no
    type ever changes. ``replacements`` maps a path to its new string.
    """
    return _replace(value, (), replacements)


def _replace(value: Any, path: Path, replacements: dict) -> Any:
    if isinstance(value, str):
        replacement = replacements.get(path)
        if replacement is None:
            return value
        if not isinstance(replacement, str):
            raise TypeError("a string leaf may only be replaced by a string")
        return replacement
    if isinstance(value, dict):
        return {
            key: (
                _replace(item, path + (key,), replacements)
                if isinstance(key, str)
                else item
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _replace(item, path + (index,), replacements)
            for index, item in enumerate(value)
        ]
    return value


@dataclass(frozen=True)
class Inspection:
    """Everything one pass over a payload learned, and what it produced."""

    __slots__ = ("value", "findings", "changed", "transforms", "markers")

    value: Any
    findings: list
    #: True when ``value`` differs from the input, by masking or by diet.
    changed: bool
    transforms: tuple
    markers: tuple


def inspect(
    value: Any,
    evaluate: Callable[[str], Any],
    transforms: tuple = (),
    scan_markers: bool = False,
) -> Inspection:
    """Walk once, and offer every string leaf to each concern in turn.

    ``evaluate`` is the pure detector. Ordinal placeholders restart per leaf,
    because each leaf is an independent piece of text the model reads on its
    own; a single counter across a whole payload would produce `<EMAIL_7>` in a
    field whose text contains one address.

    The three concerns stay separate because masking rewrites, diet rewrites,
    and injection markers only report. Sharing the walk costs nothing and
    keeps a marker from ever reaching a replacement.
    """
    found = walk(value)
    replacements = {}
    findings = []
    applied: set = set()
    markers: set = set()
    for path, text in found.leaves:
        decision = evaluate(text)
        current = text
        if decision.findings:
            findings.append((path, decision))
            current = decision.redacted_text
        if scan_markers:
            from . import injection

            markers.update(injection.scan(current))
        if transforms:
            from . import diet

            current, names = diet.shrink(current, transforms)
            applied.update(names)
        if current != text:
            replacements[path] = current
    ordered_transforms = _ordered(applied)
    ordered_markers = _markers(markers)
    if not replacements:
        return Inspection(value, findings, False, ordered_transforms, ordered_markers)
    return Inspection(
        replace(value, replacements),
        findings,
        True,
        ordered_transforms,
        ordered_markers,
    )


def _ordered(applied: set) -> tuple:
    from . import diet

    return tuple(name for name in diet.TRANSFORMS if name in applied)


def _markers(markers: set) -> tuple:
    from . import injection

    return tuple(name for name in injection.MARKERS if name in markers)


def mask(value: Any, evaluate: Callable[[str], Any]) -> tuple:
    """Mask only, as ``(rewritten, findings, changed)``."""
    result = inspect(value, evaluate)
    return result.value, result.findings, result.changed
