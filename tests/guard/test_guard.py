from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from presidio_analyzer import RecognizerResult

from shim_guard.guard import (
    ENTITY_TYPES,
    MAX_FINDINGS,
    Finding,
    GuardDecision,
    analyze,
    evaluate,
)
from shim_guard.guard.normalize import normalize
from shim_guard.guard.recognizers import OfflineEmailRecognizer, analyzer

CORPUS = json.loads(
    (Path(__file__).parents[1] / "corpus" / "guard-v1.json").read_text()
)
METRICS = json.loads(
    (Path(__file__).parents[1] / "corpus" / "guard-v1-metrics.json").read_text()
)


@pytest.mark.parametrize("case", CORPUS["cases"], ids=lambda case: case["id"])
def test_public_corpus(case: dict[str, object]) -> None:
    decision = evaluate(str(case["text"]))

    assert [finding.entity_type for finding in decision.findings] == case["categories"]
    assert decision.blocked is bool(case["categories"])
    if "redacted" in case:
        assert decision.redacted_text == case["redacted"]


def test_corpus_has_positive_and_safe_negative_for_every_public_category() -> None:
    assert CORPUS["version"] == 1
    assert tuple(CORPUS["categories"]) == ENTITY_TYPES
    positive = {category for case in CORPUS["cases"] for category in case["categories"]}
    negative_ids = {case["id"] for case in CORPUS["cases"] if not case["categories"]}

    assert positive == set(ENTITY_TYPES)
    assert {
        f"{category.lower().replace('_', '-')}-safe-negative"
        for category in ENTITY_TYPES
    } <= negative_ids


def test_published_corpus_metrics_match_detector_predictions() -> None:
    predictions = {
        case["id"]: {finding.entity_type for finding in analyze(case["text"])}
        for case in CORPUS["cases"]
    }
    categories: dict[str, dict[str, int | float]] = {}
    for category in ENTITY_TYPES:
        expected = {
            case["id"] for case in CORPUS["cases"] if category in case["categories"]
        }
        predicted = {
            case_id
            for case_id, predicted_categories in predictions.items()
            if category in predicted_categories
        }
        true_positive = len(expected & predicted)
        false_positive = len(predicted - expected)
        false_negative = len(expected - predicted)
        categories[category] = {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "negative_cases": len(CORPUS["cases"]) - len(expected),
            "precision": true_positive / (true_positive + false_positive),
            "recall": true_positive / (true_positive + false_negative),
        }
    micro = {
        key: sum(category[key] for category in categories.values())
        for key in (
            "true_positive",
            "false_positive",
            "false_negative",
            "negative_cases",
        )
    }
    micro["precision"] = micro["true_positive"] / (
        micro["true_positive"] + micro["false_positive"]
    )
    micro["recall"] = micro["true_positive"] / (
        micro["true_positive"] + micro["false_negative"]
    )

    assert METRICS == {
        "schema_version": 1,
        "corpus": {
            "file": "guard-v1.json",
            "version": CORPUS["version"],
            "case_count": len(CORPUS["cases"]),
        },
        "evaluation_unit": "case-category presence",
        "claim_scope": (
            "Synthetic fixture-bound evidence only; not a real-world statistical claim."
        ),
        "acceptance_thresholds": {"precision": 1.0, "recall": 1.0},
        "categories": categories,
        "micro": micro,
    }
    for metrics in (*categories.values(), micro):
        assert metrics["precision"] >= METRICS["acceptance_thresholds"]["precision"]
        assert metrics["recall"] >= METRICS["acceptance_thresholds"]["recall"]


def test_models_are_immutable_and_counts_follow_first_source_occurrence() -> None:
    later = Finding("EMAIL", 20, 30, 0.9)
    first = Finding("PHONE", 0, 10, 0.8)
    decision = GuardDecision((later, first, Finding("EMAIL", 40, 50, 0.7)), "x")

    assert decision.counts == (("PHONE", 1), ("EMAIL", 2))
    with pytest.raises((AttributeError, TypeError)):
        decision.redacted_text = "changed"  # type: ignore[misc]


def test_ordinals_are_per_category_in_source_order_without_a_value_map() -> None:
    decision = evaluate("alice@example.com +90 532 123 45 67 bob@example.com")

    assert decision.redacted_text == "<EMAIL_1> <PHONE_1> <EMAIL_2>"
    assert decision.counts == (("EMAIL", 2), ("PHONE", 1))
    assert not hasattr(decision, "replacement_map")
    assert not hasattr(decision, "raw_values")


def test_source_normalized_intermediate_and_finding_limits() -> None:
    generated = {case["id"]: case for case in CORPUS["generated_cases"]}
    with pytest.raises(ValueError, match="safe analysis limit"):
        analyze(
            generated["source-oversize"]["value"]
            * generated["source-oversize"]["count"]
        )
    with pytest.raises(ValueError, match="safe analysis limit"):
        normalize(
            generated["normalization-intermediate-oversize"]["value"]
            * generated["normalization-intermediate-oversize"]["count"]
        )
    emails = " ".join(
        f"user{index}@example.com"
        for index in range(generated["finding-count-oversize"]["count"])
    )
    with pytest.raises(ValueError, match="finding limit"):
        analyze(emails)
    assert (
        len(analyze(" ".join(f"u{i}@example.com" for i in range(MAX_FINDINGS))))
        == MAX_FINDINGS
    )


def test_invalid_or_incomplete_analyzer_spans_fail_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("shim_guard.guard.analyze")

    class FakeAnalyzer:
        def analyze(self, **_: object) -> list[RecognizerResult]:
            return [RecognizerResult("EMAIL_ADDRESS", 0, 999, 0.9)]

    monkeypatch.setattr(module, "analyzer", lambda: FakeAnalyzer())
    with pytest.raises(ValueError, match="invalid span"):
        module.analyze("safe")


def test_shared_analysis_deadline_fails_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("shim_guard.guard.analyze")

    class SlowAnalyzer:
        def analyze(self, **_: object) -> list[RecognizerResult]:
            time.sleep(1)
            return []

    monkeypatch.setattr(module, "ANALYSIS_DEADLINE_SECONDS", 0.05)
    monkeypatch.setattr(module, "analyzer", lambda: SlowAnalyzer())
    with pytest.raises(ValueError, match="runtime limit"):
        module.analyze("safe")


def test_adversarial_punctuation_completes_within_the_detector_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("shim_guard.guard.analyze")
    analyzer.cache_clear()
    monkeypatch.setattr(module, "ANALYSIS_DEADLINE_SECONDS", 3)

    started = time.monotonic()
    findings = module.analyze('"' * 12_000 + " alice@example.com")

    assert time.monotonic() - started < 3
    assert [finding.entity_type for finding in findings] == ["EMAIL"]


def test_malformed_percent_encoded_utf8_fails_safely() -> None:
    with pytest.raises(ValueError, match="malformed percent encoding"):
        analyze("alice%C3%28@example.com")


def test_overlap_tie_is_deterministic_and_covers_the_component() -> None:
    module = importlib.import_module("shim_guard.guard.analyze")
    resolved = module._resolve_overlaps(
        [
            Finding("TR_VKN", 0, 8, 0.8),
            Finding("TR_NATIONAL_ID", 4, 12, 0.8),
        ]
    )

    assert resolved == [Finding("TR_NATIONAL_ID", 0, 12, 0.8)]


def test_analyzer_is_cached_and_email_validation_is_explicitly_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer.cache_clear()
    assert analyzer() is analyzer()
    extractor = OfflineEmailRecognizer._extract
    assert extractor.suffix_list_urls == ()
    assert extractor._cache.enabled is False
    monkeypatch.setattr(extractor, "_extractor", None)

    def no_network(*_: object, **__: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr("socket.socket.connect", no_network)
    assert analyze("alice@example.com")[0].entity_type == "EMAIL"


def test_results_are_independent_of_python_hash_seed() -> None:
    case = next(
        case
        for case in CORPUS["generated_cases"]
        if case["id"] == "deterministic-hash-seed"
    )
    command = [
        sys.executable,
        "-c",
        "from shim_guard.guard import evaluate; print(evaluate('alice@example.com 192.168.1.1'))",
    ]
    outputs = []
    for seed in case["seeds"]:
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        outputs.append(
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            ).stdout
        )
    assert outputs[0] == outputs[1]
