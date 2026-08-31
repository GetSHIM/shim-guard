"""Generate the frozen ICANN public suffix table."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

HEADER = '''"""Generated ICANN suffixes; do not edit."""

from __future__ import annotations

SOURCE = (
    {source}
)

_RULES = """\\
{blob}"""

_SUFFIXES = frozenset(_RULES.split("\\n"))


def public_suffix(host: str) -> str:
    labels = host.split(".")
    for index in range(len(labels)):
        candidate = ".".join(labels[index:])
        if "!" + candidate in _SUFFIXES:
            return ".".join(labels[index + 1 :])
        if candidate in _SUFFIXES:
            return candidate
        if index and "*." + ".".join(labels[index:]) in _SUFFIXES:
            return ".".join(labels[index - 1 :])
    return ""


def is_registrable(host: str) -> bool:
    normalized = host.lower().rstrip(".")
    if not normalized or "" in normalized.split("."):
        return False
    suffix = public_suffix(normalized)
    if not suffix or suffix == normalized:
        return False
    return bool(normalized[: -len(suffix) - 1])
'''


def punycode(suffix: str) -> str:
    labels = []
    for label in suffix.split("."):
        if label.isascii():
            labels.append(label)
            continue
        labels.append(label.encode("idna").decode("ascii"))
    return ".".join(labels)


def icann_rules(snapshot: str) -> list[str]:
    rules: set[str] = set()
    private = False
    for line in snapshot.splitlines():
        text = line.strip()
        if text.startswith("// ===BEGIN PRIVATE DOMAINS==="):
            private = True
            continue
        if text.startswith("// ===END PRIVATE DOMAINS==="):
            private = False
            continue
        if private or not text or text.startswith("//"):
            continue
        rules.add(text)
        marker = ""
        body = text
        if text[0] in "*!":
            marker = text[:2] if text.startswith("*.") else text[0]
            body = text[len(marker) :]
        try:
            ascii_form = punycode(body)
        except UnicodeError:
            continue
        if ascii_form != body:
            rules.add(marker + ascii_form)
    return sorted(rules)


def render(rules: list[str], source: str) -> str:
    if any('"' in rule or "\\" in rule for rule in rules):
        raise ValueError("a suffix rule would break the string literal")
    return HEADER.format(
        source=json.dumps(source),
        blob="\n".join(rules),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", default="")
    args = parser.parse_args()
    rules = icann_rules(args.snapshot.read_text(encoding="utf-8"))
    source = args.source or f"publicsuffix.org ICANN section, via {args.snapshot.name}"
    args.output.write_text(render(rules, source), encoding="utf-8")
    print(f"{len(rules)} rules -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
