"""The hook path must not pull in third-party code beyond ``phonenumbers``.

PRD-02 removed presidio-analyzer and tldextract from the hook path so a cold
process stays inside the latency budget and so the zipapp in PRD-04 can run on
a system interpreter. Nothing enforces that except this test: an accidental
top-level ``import rich`` in a shared module would restore the old cost without
failing anything else.

``-X importtime`` cannot be used for this. ``hook._silence_dependencies``
redirects file descriptor 2 while the detector runs, which is exactly the
window the imports happen in, so the trace is incomplete. Inspecting
``sys.modules`` afterwards is not defeated by the redirection.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

# `tomli` is the stdlib `tomllib` backported, imported only below Python 3.11
# and only to read the user's own settings file. Above 3.11 the stdlib module
# is used and nothing third-party is loaded at all.
ALLOWED_THIRD_PARTY = frozenset({"phonenumbers", "tomli"})
FORBIDDEN = ("presidio_analyzer", "tldextract", "spacy", "typer", "rich", "click")

_PROBE = """
import importlib.util
import json
import sys

from shim_guard import hook

payload = json.dumps(
    {"hook_event_name": "UserPromptSubmit", "prompt": PROMPT}
).encode()
if CLIENT == "copilot":
    payload = json.dumps(
        {"prompt": PROMPT, "transformedPrompt": PROMPT}
    ).encode()

output = hook._output(payload, CLIENT)

third_party = []
for name in sorted({module.split(".")[0] for module in sys.modules}):
    if name.startswith("_"):
        continue
    try:
        specification = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        continue
    if specification and specification.origin and "site-packages" in specification.origin:
        third_party.append(name)

shim_modules = sorted(m for m in sys.modules if m.startswith("shim_guard."))
sys.stdout.write(json.dumps(
    {"third_party": third_party, "bytes": len(output), "shim": shim_modules}
))
"""


def _probe(client: str, prompt: str) -> dict:
    source = _PROBE.replace("CLIENT", repr(client)).replace("PROMPT", repr(prompt))
    result = subprocess.run(
        (sys.executable, "-I", "-B", "-c", source),
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr.decode()
    return json.loads(result.stdout)


@pytest.mark.parametrize("client", ("codex", "claude", "copilot"))
@pytest.mark.parametrize(
    "prompt",
    (
        "Explain merge sort.",
        "Contact alice@example.com and call +90 532 123 45 67",
    ),
)
def test_the_hook_path_imports_no_unexpected_third_party_module(
    client: str, prompt: str
) -> None:
    observed = _probe(client, prompt)
    unexpected = set(observed["third_party"]) - ALLOWED_THIRD_PARTY

    assert not unexpected, (
        f"{client}: the hook path imported {sorted(unexpected)}. "
        f"Only {sorted(ALLOWED_THIRD_PARTY)} are permitted; "
        "move the import into the CLI."
    )
    assert observed["bytes"] >= 0


@pytest.mark.parametrize("module", FORBIDDEN)
def test_named_heavy_dependencies_never_reach_the_hook_path(module: str) -> None:
    observed = _probe("codex", "Contact alice@example.com")

    assert module not in observed["third_party"]


def test_the_detector_alone_imports_nothing_third_party() -> None:
    """``guard`` is the piece PRD-04 packages; it must be standard library."""
    source = (
        "import importlib.util, json, sys\n"
        "from shim_guard.guard import evaluate\n"
        "evaluate('Contact alice@example.com')\n"
        "names = sorted({m.split('.')[0] for m in sys.modules})\n"
        "third = []\n"
        "for name in names:\n"
        "    if name.startswith('_'):\n"
        "        continue\n"
        "    try:\n"
        "        spec = importlib.util.find_spec(name)\n"
        "    except (ImportError, ValueError):\n"
        "        continue\n"
        "    if spec and spec.origin and 'site-packages' in spec.origin:\n"
        "        third.append(name)\n"
        "sys.stdout.write(json.dumps(third))\n"
    )
    result = subprocess.run(
        (sys.executable, "-I", "-B", "-c", source),
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert set(json.loads(result.stdout)) <= ALLOWED_THIRD_PARTY


@pytest.mark.parametrize("client", ("codex", "claude", "copilot"))
def test_the_hook_path_never_imports_the_watch_proxy(client: str) -> None:
    """`shim watch` runs once per session; the hook runs per tool call.

    The proxy pulls in `http.server`, `http.client`, `ssl` and `socketserver`.
    None of that is free, and the hook pays every import on every tool call, so
    the two must not share a module. Nothing else enforces the boundary.
    """
    observed = _probe(client, "Contact alice@example.com")

    watch = [name for name in observed["shim"] if name.startswith("shim_guard.watch")]

    assert not watch, f"{client}: the hook path imported {watch}"
