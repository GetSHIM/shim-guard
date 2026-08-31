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

Detection is deliberately reporting-only. Acting on a marker is a later
decision that needs the data this produces.
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
_INVISIBLE = re.compile("[\u200b\u2060-\u2064\u202a-\u202e\U000e0000-\U000e007f]")

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
            r"\b(?:you\s{1,4}are\s{1,4}now|from\s{1,4}now\s{1,4}on\s{1,4}you"
            r"|act\s{1,4}as\s{1,4}(?:a|an|the)\b"
            r"|pretend\s{1,4}(?:to\s{1,4}be|you\s{1,4}are))",
            re.IGNORECASE,
        ),
    ),
    (
        SYSTEM_IMPERSONATION,
        re.compile(
            r"(?:^|\n)[^\S\n]{0,8}(?:\[|<|\#{1,3}[^\S\n]{0,4})?"
            r"(?:system|assistant)[^\S\n]{0,4}(?:\]|>|:)"
            r"|<[^\S\n]{0,4}/?[^\S\n]{0,4}"
            r"(?:system|important_instructions)[^\S\n]{0,4}>",
            re.IGNORECASE,
        ),
    ),
    (
        SECRECY_REQUEST,
        re.compile(
            r"\b(?:do\s{0,4}not|don'?t|never)\b[^.\n]{0,30}?"
            r"\b(?:tell|inform|mention|reveal|show|disclose)\b[^.\n]{0,20}?"
            r"\b(?:the\s{1,4})?(?:user|human|operator)\b",
            re.IGNORECASE,
        ),
    ),
)

#: A marker is only worth reporting on text long enough to be a payload rather
#: than a fragment; a bare "you are now" in a two-word field is noise.
MIN_TEXT_CHARACTERS = 24

#: Every quantifier above is bounded on purpose. An unbounded `\s*` behind the
#: `(?:^|\n)` anchor is re-entered at every newline and walks the rest of the
#: run each time, which is quadratic: 16k blank lines cost 6.2s and 32k cost
#: 25s, enough to burn the hook deadline on a file anyone can put in a repo.
#: `tests/events/test_injection.py` holds the timing that proves it stays flat.


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
