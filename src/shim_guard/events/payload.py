from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

MAX_TEXT_CHARACTERS = 200_000
MAX_DEPTH = 24
MAX_LEAVES = 2_000

Path = tuple


class PayloadTooLarge(ValueError):
    pass


@dataclass
class Traversal:
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
                _walk(item, (*path, key), found, depth + 1)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _walk(item, (*path, index), found, depth + 1)


def replace(value: Any, replacements: dict) -> Any:
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
                _replace(item, (*path, key), replacements)
                if isinstance(key, str)
                else item
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _replace(item, (*path, index), replacements)
            for index, item in enumerate(value)
        ]
    return value


@dataclass(frozen=True)
class Inspection:
    __slots__ = ("value", "findings", "changed", "transforms", "markers")

    value: Any
    findings: list
    changed: bool
    transforms: tuple
    markers: tuple


def inspect(
    value: Any,
    evaluate: Callable[[str], Any],
    transforms: tuple = (),
    scan_markers: bool = False,
) -> Inspection:
    found = walk(value)
    transform_order = ()
    apply_diet = None
    if transforms:
        from .diet import TRANSFORMS, shrink

        transform_order = TRANSFORMS
        apply_diet = shrink
    marker_order = ()
    scan_injection = None
    if scan_markers:
        from .injection import MARKERS, scan

        marker_order = MARKERS
        scan_injection = scan
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
        if scan_injection is not None:
            markers.update(scan_injection(current))
        if apply_diet is not None:
            current, names = apply_diet(current, transforms)
            applied.update(names)
        if current != text:
            replacements[path] = current
    ordered_transforms = tuple(name for name in transform_order if name in applied)
    ordered_markers = tuple(name for name in marker_order if name in markers)
    if not replacements:
        return Inspection(value, findings, False, ordered_transforms, ordered_markers)
    return Inspection(
        replace(value, replacements),
        findings,
        True,
        ordered_transforms,
        ordered_markers,
    )


def mask(value: Any, evaluate: Callable[[str], Any]) -> tuple:
    result = inspect(value, evaluate)
    return result.value, result.findings, result.changed
