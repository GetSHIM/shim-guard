"""Pure Guard decision and typed ordinal redaction."""

from __future__ import annotations

from .analyze import analyze
from .models import GuardDecision


def evaluate(text: str) -> GuardDecision:
    findings = analyze(text)
    if not findings:
        return GuardDecision((), text)

    counts: dict[str, int] = {}
    pieces: list[str] = []
    cursor = 0
    for finding in findings:
        counts[finding.entity_type] = counts.get(finding.entity_type, 0) + 1
        pieces.append(text[cursor : finding.start])
        pieces.append(f"<{finding.entity_type}_{counts[finding.entity_type]}>")
        cursor = finding.end
    pieces.append(text[cursor:])
    return GuardDecision(findings, "".join(pieces))
