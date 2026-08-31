"""A store listing is the first thing anyone reads, and it makes a promise.

PRD-10 R5: the boundary that applies to the README applies here. shim measures
and detects; with the shipped policy it does not stop a secret a person typed
from reaching the model, it reports it afterwards. A listing that claims
otherwise is the one place a user cannot check before trusting it.

The listing said "Block sensitive prompt data before it reaches coding-agent
models", which is exactly the claim the README's own warning contradicts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLAUDE = ROOT / ".claude-plugin" / "marketplace.json"
CODEX = ROOT / ".agents" / "plugins" / "marketplace.json"

#: Words that promise protection rather than detection. `prevent` is named in
#: R5; the rest are the same claim wearing a different verb.
FORBIDDEN = (
    "prevent",
    "guarantee",
    "block sensitive",
    "blocks sensitive",
    "stop secrets",
    "stops secrets",
    "never leaves",
    "100%",
)


def _text(path: Path) -> str:
    return json.dumps(json.loads(path.read_text()), ensure_ascii=False).lower()


@pytest.mark.parametrize("path", (CLAUDE, CODEX), ids=("claude", "codex"))
def test_a_listing_never_promises_protection(path: Path) -> None:
    listing = _text(path)

    found = [word for word in FORBIDDEN if word in listing]

    assert not found, (
        f"{path.name} claims {found}. shim detects and reports; with the "
        "shipped policy it does not stop a typed secret from reaching the "
        "model. See the warning at the top of README.md."
    )


@pytest.mark.parametrize("path", (CLAUDE, CODEX), ids=("claude", "codex"))
def test_a_listing_points_at_a_plugin_that_exists(path: Path) -> None:
    document = json.loads(path.read_text())

    for plugin in document["plugins"]:
        source = plugin["source"]
        relative = source if isinstance(source, str) else source["path"]
        assert (ROOT / relative).is_dir(), relative
        assert (ROOT / relative / "hooks").is_dir()


def test_the_claude_listing_claims_masking_because_claude_can_mask() -> None:
    description = json.loads(CLAUDE.read_text())["plugins"][0]["description"].lower()

    assert "mask" in description, "Claude Code masks tool results — say so"
    assert "tool results" in description
    assert "no network" in description or "locally" in description


def test_the_codex_listing_does_not_claim_masking_because_codex_cannot() -> None:
    """Codex has no verified surgical tool-result rewrite."""

    description = json.loads(CODEX.read_text())["plugins"][0]["description"].lower()

    assert "mask" not in description
    assert "redact" not in description
    assert "told" in description or "report" in description


@pytest.mark.parametrize("path", (CLAUDE, CODEX), ids=("claude", "codex"))
def test_a_listing_leads_with_what_the_user_gets(path: Path) -> None:
    """Both stores show one line. It has to say something a person wants."""
    description = json.loads(path.read_text())["plugins"][0]["description"]

    assert len(description) <= 300, "stores truncate; keep it readable"
    assert description[0].isupper() and description.rstrip().endswith(".")


def test_both_listings_name_the_same_plugin() -> None:
    claude = {plugin["name"] for plugin in json.loads(CLAUDE.read_text())["plugins"]}
    codex = {plugin["name"] for plugin in json.loads(CODEX.read_text())["plugins"]}

    assert claude == codex == {"shim-guard"}
