"""Flag text in a tool result that is trying to give the model orders.

This is a different question from the detector's. The detector asks whether a
span *is* an email or a key — a question about identity, answered by patterns
and validators. This asks whether text is addressed to the model as an
instruction, which is a question about intent, and intent has no validator.

Two consequences follow and both are deliberate:

* **A marker never masks anything.** It is reported and nothing else. Rewriting
  a tool result because it reads as imperative would corrupt legitimate
  content — a code review, a style guide, documentation about prompt injection
  — and would be a far worse failure than the one it prevents.
* **Markers are not entities.** They stay out of `enabled_entities` and out of
  the masking path entirely, so no configuration can turn one into a rewrite.

Detection only, per PRD-07 R7. What to do about a marker is a later decision
that needs data this produces.
"""

from __future__ import annotations

import re

INSTRUCTION_OVERRIDE = "INSTRUCTION_OVERRIDE"
ROLE_REASSIGNMENT = "ROLE_REASSIGNMENT"
SYSTEM_IMPERSONATION = "SYSTEM_IMPERSONATION"
SECRECY_REQUEST = "SECRECY_REQUEST"
HIDDEN_TEXT = "HIDDEN_TEXT"

MARKERS = (
    INSTRUCTION_OVERRIDE,
    ROLE_REASSIGNMENT,
    SYSTEM_IMPERSONATION,
    SECRECY_REQUEST,
    HIDDEN_TEXT,
)

#: Characters that render as nothing, so text carrying them says one thing to
#: a reader and another to the model. The tag block (U+E0000) is the one used
#: to smuggle whole instructions past a human reviewer.
_INVISIBLE = re.compile("[​-‏⁠-⁤‪-‮﻿\U000e0000-\U000e007f]")

_PATTERNS = (
    (
        INSTRUCTION_OVERRIDE,
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,40}?"
            r"\b(?:previous|prior|above|earlier|all)\b[^.\n]{0,20}?"
            r"\b(?:instruction|prompt|rule|direction|command)s?\b",
            re.IGNORECASE,
        ),
    ),
    (
        ROLE_REASSIGNMENT,
        re.compile(
            r"\b(?:you\s+are\s+now|from\s+now\s+on\s+you|act\s+as\s+(?:a|an|the)\b"
            r"|pretend\s+(?:to\s+be|you\s+are))",
            re.IGNORECASE,
        ),
    ),
    (
        SYSTEM_IMPERSONATION,
        re.compile(
            r"(?:^|\n)\s*(?:\[|<|#{1,3}\s*)?(?:system|assistant)\s*(?:\]|>|:)"
            r"|<\s*/?\s*(?:system|important_instructions)\s*>",
            re.IGNORECASE,
        ),
    ),
    (
        SECRECY_REQUEST,
        re.compile(
            r"\b(?:do\s*not|don'?t|never)\b[^.\n]{0,30}?"
            r"\b(?:tell|inform|mention|reveal|show|disclose)\b[^.\n]{0,20}?"
            r"\b(?:the\s+)?(?:user|human|operator)\b",
            re.IGNORECASE,
        ),
    ),
)

#: A marker is only worth reporting on text long enough to be a payload rather
#: than a fragment; a bare "you are now" in a two-word field is noise.
MIN_TEXT_CHARACTERS = 24


def scan(text: str) -> tuple:
    """Return the marker names present in one string leaf, in a stable order."""
    if len(text) < MIN_TEXT_CHARACTERS:
        return ()
    found = []
    if _INVISIBLE.search(text):
        found.append(HIDDEN_TEXT)
    for name, pattern in _PATTERNS:
        if pattern.search(text):
            found.append(name)
    return tuple(name for name in MARKERS if name in found)


__all__ = [
    "HIDDEN_TEXT",
    "INSTRUCTION_OVERRIDE",
    "MARKERS",
    "MIN_TEXT_CHARACTERS",
    "ROLE_REASSIGNMENT",
    "SECRECY_REQUEST",
    "SYSTEM_IMPERSONATION",
    "scan",
]
