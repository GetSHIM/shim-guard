"""Curated Presidio recognizers which cannot fetch runtime data."""

from __future__ import annotations

import re
from functools import lru_cache

from presidio_analyzer import (
    AnalyzerEngine,
    EntityRecognizer,
    Pattern,
    PatternRecognizer,
    RecognizerRegistry,
    RecognizerResult,
)
from presidio_analyzer.nlp_engine import NlpArtifacts, NoOpNlpEngine
from presidio_analyzer.predefined_recognizers import (
    CreditCardRecognizer,
    EmailRecognizer,
    IbanRecognizer,
    IpRecognizer,
    MacAddressRecognizer,
    PhoneRecognizer,
    TrNationalIdRecognizer,
    UsSsnRecognizer,
)
from tldextract import TLDExtract

LANGUAGE = "tr"
ENTITY_MAP = {
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "PHONE",
    "CREDIT_CARD": "CREDIT_CARD",
    "IBAN_CODE": "IBAN",
    "IP_ADDRESS": "IP_ADDRESS",
    "MAC_ADDRESS": "MAC_ADDRESS",
    "US_SSN": "US_SSN",
    "TR_NATIONAL_ID": "TR_NATIONAL_ID",
    "TR_VKN": "TR_VKN",
    "SECRET": "SECRET",
    "DB_URI": "DB_URI",
    "FILE_PATH": "FILE_PATH",
}

_TRAILING_PROSE = ".,;:!?)]}>"


class OfflineEmailRecognizer(EmailRecognizer):
    _extract = TLDExtract(suffix_list_urls=(), cache_dir=None)

    def validate_result(self, pattern_text: str) -> bool:
        domain = self._extract(pattern_text)
        return bool(domain.fqdn and domain.suffix)


class SecretRecognizer(EntityRecognizer):
    _KEY = (
        r"password|passwd|pwd|api[_-]?key|secret|token|db[_-]?pass|"
        r"postgres_password"
    )
    _ASSIGNMENT = rf"(?<![\w-])[\"']?(?:{_KEY})[\"']?\s*(?:=|:)\s*"
    _PATTERNS: tuple[tuple[re.Pattern[str], str | None, float, bool], ...] = (
        (
            re.compile(
                r"-----BEGIN "
                r"(?P<key_type>(?:(?:RSA|EC|OPENSSH|ENCRYPTED) )?PRIVATE KEY)"
                r"-----[\s\S]*?(?:-----END (?P=key_type)-----|\Z)"
            ),
            None,
            0.99,
            False,
        ),
        (
            re.compile(
                r"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|"
                r"sk_(?:live|test)_[A-Za-z0-9]{16,}|"
                r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
                r"SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}|"
                r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)"
            ),
            None,
            0.99,
            False,
        ),
        (
            re.compile(
                r"https://(?:hooks\.slack\.com/services|"
                r"discord(?:app)?\.com/api/webhooks)/[^\s'\"]+",
                re.IGNORECASE,
            ),
            None,
            0.99,
            True,
        ),
        (
            re.compile(
                rf"{_ASSIGNMENT}(?P<quote>[\"'])(?P<value>[^\r\n]{{6,}}?)(?P=quote)",
                re.IGNORECASE,
            ),
            "value",
            0.97,
            False,
        ),
        (
            re.compile(rf"{_ASSIGNMENT}(?P<value>[^\s,}}\]\"']{{6,}})", re.IGNORECASE),
            "value",
            0.97,
            False,
        ),
        (
            re.compile(
                r"--password(?:=|\s+)(?P<quote>[\"'])"
                r"(?P<value>[^\r\n]{6,}?)(?P=quote)",
                re.IGNORECASE,
            ),
            "value",
            0.97,
            False,
        ),
        (
            re.compile(r"--password(?:=|\s+)(?P<value>[^\s]+)", re.IGNORECASE),
            "value",
            0.97,
            False,
        ),
    )

    def __init__(self) -> None:
        super().__init__(supported_entities=["SECRET"], supported_language=LANGUAGE)

    def load(self) -> None:
        pass

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: NlpArtifacts | None,
    ) -> list[RecognizerResult]:
        if "SECRET" not in entities:
            return []
        results: list[RecognizerResult] = []
        for pattern, value_group, score, trim in self._PATTERNS:
            for match in pattern.finditer(text):
                start, end = match.span(value_group) if value_group else match.span()
                if trim:
                    end = _trim_trailing_prose(text, start, end)
                if start < end:
                    results.append(RecognizerResult("SECRET", start, end, score))
        return results


class UriAndPathRecognizer(EntityRecognizer):
    _PATTERNS = (
        (
            "DB_URI",
            re.compile(
                r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis(?:s)?|"
                r"mssql)://[^\s'\"<>]+"
            ),
            0.99,
        ),
        (
            "FILE_PATH",
            re.compile(
                r"(?<![\w:/])(?:/(?:Users|home|var|etc|opt|srv|tmp)/"
                r"[^\s,;\"'<>|]+|[A-Za-z]:\\[^\s,;\"'<>|]+)"
            ),
            0.85,
        ),
    )

    def __init__(self) -> None:
        super().__init__(
            supported_entities=["DB_URI", "FILE_PATH"], supported_language=LANGUAGE
        )

    def load(self) -> None:
        pass

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: NlpArtifacts | None,
    ) -> list[RecognizerResult]:
        enabled = frozenset(entities)
        results: list[RecognizerResult] = []
        for entity_type, pattern, score in self._PATTERNS:
            if entity_type not in enabled:
                continue
            for match in pattern.finditer(text):
                start, end = match.span()
                end = _trim_trailing_prose(text, start, end)
                if start < end:
                    results.append(RecognizerResult(entity_type, start, end, score))
        return results


class TurkishTaxIdRecognizer(PatternRecognizer):
    COUNTRY_CODE = "tr"
    _CONTEXT = re.compile(
        r"(?i)\b(?:vkn|vergi(?:\s+(?:kimlik|numarası))?|tax(?:\s+id)?)\b"
    )

    def __init__(self) -> None:
        super().__init__(
            name="ShimTurkishTaxIdRecognizer",
            supported_entity="TR_VKN",
            supported_language=LANGUAGE,
            context=["vergi", "vkn", "tax", "vergi kimlik", "vergi numarası"],
            patterns=[Pattern("Turkish tax ID", r"(?<!\d)\d{10}(?!\d)", 0.4)],
        )

    def validate_result(self, pattern_text: str) -> bool:
        digits = [int(character) for character in pattern_text]
        if len(digits) != 10 or len(set(digits)) == 1:
            return False
        checksum = 0
        for index, digit in enumerate(digits[:9]):
            adjusted = (digit + 9 - index) % 10
            if adjusted:
                checksum += (adjusted * 2 ** (9 - index)) % 9
        return digits[-1] == (10 - checksum % 10) % 10

    def analyze(
        self,
        text: str,
        entities: list[str],
        nlp_artifacts: NlpArtifacts | None = None,
        regex_flags: int | None = None,
    ) -> list[RecognizerResult]:
        results = super().analyze(text, entities, nlp_artifacts, regex_flags)
        return [
            result
            for result in results
            if self._CONTEXT.search(
                text[max(0, result.start - 32) : min(len(text), result.end + 32)]
            )
        ]


def _trim_trailing_prose(text: str, start: int, end: int) -> int:
    while end > start and text[end - 1] in _TRAILING_PROSE:
        end -= 1
    return end


@lru_cache(maxsize=1)
def analyzer() -> AnalyzerEngine:
    registry = RecognizerRegistry(
        recognizers=[
            OfflineEmailRecognizer(
                supported_language=LANGUAGE,
                context=["email", "e-posta", "mail"],
            ),
            PhoneRecognizer(
                supported_language=LANGUAGE,
                supported_regions=(*PhoneRecognizer.DEFAULT_SUPPORTED_REGIONS, "TR"),
                context=[*PhoneRecognizer.CONTEXT, "telefon", "cep", "gsm"],
            ),
            CreditCardRecognizer(supported_language=LANGUAGE),
            IbanRecognizer(supported_language=LANGUAGE),
            IpRecognizer(supported_language=LANGUAGE),
            MacAddressRecognizer(supported_language=LANGUAGE),
            UsSsnRecognizer(supported_language=LANGUAGE),
            TrNationalIdRecognizer(supported_language=LANGUAGE),
            TurkishTaxIdRecognizer(),
            SecretRecognizer(),
            UriAndPathRecognizer(),
        ],
        supported_languages=[LANGUAGE],
    )
    nlp_engine = NoOpNlpEngine(models=[{"lang_code": LANGUAGE, "model_name": ""}])
    return AnalyzerEngine(
        registry=registry,
        nlp_engine=nlp_engine,
        supported_languages=[LANGUAGE],
        log_decision_process=False,
    )
