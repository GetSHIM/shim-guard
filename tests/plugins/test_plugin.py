from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import shim_guard
from shim_guard.clients.claude import settings as claude_settings
from shim_guard.clients.codex import settings as codex_settings

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

PLUGIN_ROOT = Path(__file__).parents[2] / "plugins" / "shim-guard"
REPOSITORY_ROOT = PLUGIN_ROOT.parents[1]


def test_plugin_versions_match_package() -> None:
    package = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    expected = package["project"]["version"]
    assert shim_guard.__version__ == expected

    for host in ("codex", "claude"):
        manifest = json.loads(
            (PLUGIN_ROOT / f".{host}-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        assert manifest["version"] == expected


@pytest.mark.parametrize(
    ("client", "manifest", "settings"),
    [
        ("claude", "claude.json", claude_settings),
        ("codex", "hooks.json", codex_settings),
    ],
)
def test_the_plugin_and_the_installer_register_the_same_events(
    client: str, manifest: str, settings: object
) -> None:
    """The two install paths must agree about coverage.

    They are alternatives, so a user on either one is entitled to the same
    events. If only one of them gained a newly verified adapter, `shim doctor`
    would report coverage that half the installed base never receives.
    """
    document = json.loads(
        (PLUGIN_ROOT / "hooks" / manifest).read_text(encoding="utf-8")
    )
    expected = {event for event, _group in settings.hook_groups()}

    assert set(document["hooks"]) == expected


@pytest.mark.parametrize("client", ["codex", "claude", "copilot"])
def test_missing_cli_allows_the_prompt(client: str) -> None:
    """A missing hook must leave the prompt unblocked.

    The launcher used to return a block decision when it could not find a hook,
    so installing the plugin alone produced an agent that refused every prompt.
    The full resolution-order and per-client contracts live in
    tests/plugins/test_launcher.py.
    """
    runner = PLUGIN_ROOT / "hooks" / "run-shim-guard"
    result = subprocess.run(
        [runner, client],
        input=b'{"hook_event_name":"UserPromptSubmit","prompt":"safe"}',
        capture_output=True,
        check=True,
        env={"PATH": "", "LC_ALL": "C"},
    )

    assert result.stdout == b""
    assert result.stderr.count(b"\n") == 1
    assert b"safe" not in result.stdout + result.stderr
    assert os.access(runner, os.X_OK)
