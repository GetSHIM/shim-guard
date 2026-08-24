from __future__ import annotations

import json
import os
import subprocess
import tomllib
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parents[2] / "plugins" / "shim-guard"
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]


def test_plugin_versions_match_package() -> None:
    package = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    expected = package["project"]["version"]

    for host in ("codex", "claude"):
        manifest = json.loads(
            (PLUGIN_ROOT / f".{host}-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        assert manifest["version"] == expected


@pytest.mark.parametrize(
    ("client", "suppresses_original"),
    [("codex", False), ("claude", True)],
)
def test_missing_cli_fails_closed(client: str, suppresses_original: bool) -> None:
    runner = PLUGIN_ROOT / "hooks" / "run-shim-guard"
    result = subprocess.run(
        [runner, client],
        input=b'{"hook_event_name":"UserPromptSubmit","prompt":"safe"}',
        capture_output=True,
        check=True,
        env={"PATH": "", "LC_ALL": "C"},
    )

    output = json.loads(result.stdout)
    assert output["decision"] == "block"
    assert output.get("suppressOriginalPrompt", False) is suppresses_original
    assert result.stderr == b""
    assert b"safe" not in result.stdout
    assert os.access(runner, os.X_OK)
