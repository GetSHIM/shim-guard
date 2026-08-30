"""Nothing committed here may carry the machine it was written on.

This is a public repository. Its documentation, fixtures and tests are largely
produced by running the tool against real sessions, and the output of a real
session is full of the operator's home directory, their editor's install path,
scratch directories and provider request identifiers. Any of it can be pasted
into a README example without anyone noticing.

The markers are read from the environment rather than written down, so this
file contains no personal data itself and protects whoever runs it — a
contributor's own username is caught on their machine, not just the
maintainer's. `tests/probe/test_probe_fixtures.py` does the same job for the
probe corpus specifically, in more detail; this is the repository-wide net.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: Paths and identifiers that only exist on a developer's machine. Scratch
#: directories are named because agent tooling writes into them and their names
#: end up in captured output.
FIXED_MARKERS = (
    "/private/tmp/claude-",
    ".vscode/extensions/anthropic",
    "Library/Application Support",
    "SHIM_fullstack",
)

#: Provider-issued identifiers that appear in real response headers and bodies.
#: A synthetic placeholder is fine; a real one is somebody's traffic.
WIRE_PATTERNS = (
    "wrkspc_01",
    "anthropic-organization-id",
)

#: Names too generic to search for without matching ordinary prose.
_GENERIC_HOME_NAMES = {"root", "runner", "user", "home", "ubuntu", "admin", "build"}

#: Files whose job is to hold these markers, so they may contain them.
EXEMPT = {
    "tests/contracts/test_no_local_leakage.py",
    "tests/probe/test_probe_fixtures.py",
}

#: Extensions worth reading. Anything else is data or binary.
TEXT_SUFFIXES = {".py", ".md", ".toml", ".json", ".yml", ".yaml", ".txt", ".cfg"}


def _tracked() -> list[str]:
    result = subprocess.run(
        ("git", "ls-files"),
        cwd=ROOT,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip("not a git checkout")
    return [line for line in result.stdout.decode().splitlines() if line]


def _markers() -> list[str]:
    """Return what would identify this machine, derived, never hard-coded."""
    found = list(FIXED_MARKERS) + list(WIRE_PATTERNS)
    try:
        home = Path.home()
    except RuntimeError:
        return found
    # The home directory itself, and the account name if it is distinctive
    # enough that finding it in a source file means something.
    found.append(str(home))
    name = home.name
    if len(name) >= 4 and name.lower() not in _GENERIC_HOME_NAMES:
        found.append(name)
    for variable in ("USER", "LOGNAME"):
        value = os.environ.get(variable, "")
        if len(value) >= 4 and value.lower() not in _GENERIC_HOME_NAMES:
            found.append(value)
    return found


MARKERS = _markers()
FILES = [
    path
    for path in _tracked()
    if path not in EXEMPT and Path(path).suffix in TEXT_SUFFIXES
]


def test_there_is_something_to_check() -> None:
    assert len(FILES) > 50, "the tracked-file listing looks wrong"
    assert MARKERS


@pytest.mark.parametrize("relative", FILES, ids=lambda path: path)
def test_no_committed_file_names_the_machine_it_was_written_on(
    relative: str,
) -> None:
    try:
        content = (ROOT / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        pytest.skip("not readable as text")

    hits = sorted({marker for marker in MARKERS if marker in content})

    assert not hits, (
        f"{relative} contains {hits}. This repository is public and its "
        "examples come from real sessions; replace the value with a synthetic "
        "one rather than trimming it."
    )


def test_the_guard_catches_a_planted_marker(tmp_path: Path) -> None:
    """The check is worthless if the markers never match anything."""
    planted = f"see {Path.home()}/notes.md for details"

    assert any(marker in planted for marker in MARKERS)


@pytest.mark.skipif(sys.platform == "win32", reason="posix paths")
def test_a_synthetic_home_is_still_allowed() -> None:
    """Fixtures need believable paths; only *this* machine's are forbidden."""
    synthetic = "Read /Users/alice/.ssh/id_rsa, then continue"

    assert not [marker for marker in MARKERS if marker in synthetic]
