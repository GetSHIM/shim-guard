"""Bounded detection orchestration and deterministic overlap handling."""

from __future__ import annotations

import contextlib
import signal
import time
from collections.abc import Iterable, Iterator

from presidio_analyzer import RecognizerResult

from shim_guard.config import ENTITY_TYPES, normalize_entities

from .models import Finding
from .normalize import normalize
from .recognizers import ENTITY_MAP, LANGUAGE, analyzer

MAX_FINDINGS = 100
ANALYSIS_DEADLINE_SECONDS = 20
_MAX_ANALYZER_RESULTS = MAX_FINDINGS * len(ENTITY_MAP)
_PRIORITY = {
    "DB_URI": 100,
    "SECRET": 90,
    "CREDIT_CARD": 80,
    "TR_NATIONAL_ID": 70,
    "TR_VKN": 70,
    "US_SSN": 70,
    "IBAN": 60,
    "MAC_ADDRESS": 50,
    "IP_ADDRESS": 40,
    "EMAIL": 30,
    "PHONE": 20,
}


@contextlib.contextmanager
def _deadline() -> Iterator[None]:
    """Bound analysis while preserving any earlier process deadline."""

    def expire(_signal_number: int, _frame: object) -> None:
        raise TimeoutError("SHIM Guard analysis deadline exceeded")

    previous_handler = signal.signal(signal.SIGALRM, expire)
    previous_delay, previous_interval = signal.getitimer(signal.ITIMER_REAL)
    delay = (
        min(ANALYSIS_DEADLINE_SECONDS, previous_delay)
        if previous_delay > 0
        else ANALYSIS_DEADLINE_SECONDS
    )
    started = time.monotonic()
    signal.setitimer(signal.ITIMER_REAL, delay)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_delay > 0:
            remaining = previous_delay - (time.monotonic() - started)
            if remaining > 0:
                signal.setitimer(signal.ITIMER_REAL, remaining, previous_interval)


def _validated(items: Iterable[RecognizerResult], text_length: int) -> list[Finding]:
    unique: dict[tuple[str, int, int], Finding] = {}
    for count, item in enumerate(items, start=1):
        if count > _MAX_ANALYZER_RESULTS:
            raise ValueError("Guard analysis exceeded the safe finding limit.")
        entity_type = getattr(item, "entity_type", None)
        start = getattr(item, "start", None)
        end = getattr(item, "end", None)
        score = getattr(item, "score", None)
        if not isinstance(entity_type, str) or entity_type not in ENTITY_MAP:
            raise ValueError("Guard analyzer returned an unsupported finding.")
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end <= start
            or end > text_length
        ):
            raise ValueError("Guard analyzer returned an invalid span.")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError("Guard analyzer returned an invalid score.")
        finding = Finding(ENTITY_MAP[entity_type], start, end, score)
        key = (finding.entity_type, start, end)
        previous = unique.get(key)
        if previous is None or finding.score > previous.score:
            unique[key] = finding
    return sorted(
        unique.values(), key=lambda item: (item.start, item.end, item.entity_type)
    )


def _resolve_overlaps(items: Iterable[Finding]) -> list[Finding]:
    ordered = sorted(items, key=lambda item: (item.start, item.end, item.entity_type))
    resolved: list[Finding] = []
    component: list[Finding] = []
    component_end = -1

    def collapse() -> None:
        if not component:
            return
        winner = min(
            component,
            key=lambda item: (
                -_PRIORITY[item.entity_type],
                -(item.end - item.start),
                item.entity_type,
                item.start,
                item.end,
                -item.score,
            ),
        )
        resolved.append(
            Finding(
                winner.entity_type,
                min(item.start for item in component),
                max(item.end for item in component),
                winner.score,
            )
        )

    for item in ordered:
        if component and item.start >= component_end:
            collapse()
            component = []
        component.append(item)
        component_end = max(component_end, item.end)
    collapse()

    for item in ordered:
        if not any(
            selected.start <= item.start and item.end <= selected.end
            for selected in resolved
        ):
            raise ValueError("Guard analyzer returned an unsafe overlap.")
    if any(
        left.end > right.start
        for left, right in zip(resolved, resolved[1:], strict=False)
    ):
        raise ValueError("Guard analyzer returned an unsafe overlap.")
    return resolved


def _source_findings(
    items: Iterable[Finding], source_spans: tuple[tuple[int, int], ...]
) -> list[Finding]:
    mapped: list[Finding] = []
    for item in items:
        spans = source_spans[item.start : item.end]
        if len(spans) != item.end - item.start or not spans:
            raise ValueError("Guard analyzer returned an incomplete span.")
        previous: tuple[int, int] | None = None
        for start, end in spans:
            if (
                start < 0
                or end <= start
                or (
                    previous is not None
                    and start < previous[1]
                    and (start, end) != previous
                )
            ):
                raise ValueError("Guard normalization returned an invalid span.")
            previous = (start, end)
        mapped.append(Finding(item.entity_type, spans[0][0], spans[-1][1], item.score))
    return _resolve_overlaps(mapped)


def analyze(
    text: str, enabled_entities: Iterable[str] = ENTITY_TYPES
) -> tuple[Finding, ...]:
    enabled = frozenset(normalize_entities(enabled_entities))
    if not enabled:
        return ()
    normalized = normalize(text)
    if not normalized.text:
        return ()
    source_entities = sorted(
        source for source, public in ENTITY_MAP.items() if public in enabled
    )
    try:
        with _deadline():
            raw = analyzer().analyze(
                text=normalized.text,
                language=LANGUAGE,
                entities=source_entities,
                score_threshold=0.4,
                return_decision_process=False,
            )
        normalized_findings = _resolve_overlaps(_validated(raw, len(normalized.text)))
    except TimeoutError as error:
        raise ValueError("Guard analysis exceeded its runtime limit.") from error
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("Guard analysis failed safely.") from error
    findings = _source_findings(normalized_findings, normalized.source_spans)
    if len(findings) > MAX_FINDINGS:
        raise ValueError("Guard analysis exceeded the safe finding limit.")
    return tuple(findings)
