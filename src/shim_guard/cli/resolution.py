"""Report which hook the plugin launcher would actually run.

`plugins/shim-guard/hooks/run-shim-guard` resolves in a fixed order — the
package on PATH, then the archive bundled in the plugin, then nothing — and the
three cases behave very differently. `shim doctor` has to be able to say which
one is live, because "installed" and "running" are not the same thing once a
plugin can carry its own copy.

Everything here is read-only and offline: the archive's version is read out of
the zip rather than by executing it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

ARCHIVE_RELATIVE = Path("bin") / "shim.pyz"
PLUGIN_NAME = "shim-guard"
MAX_MANIFEST_BYTES = 1_000_000
_VERSION = re.compile(r'^__version__ = "(?P<version>[0-9][0-9A-Za-z.+-]*)"', re.M)


@dataclass(frozen=True)
class Resolution:
    """Which hook would run, and anything worth warning about."""

    __slots__ = ("source", "detail", "path_version", "archive_version")

    source: str
    detail: str
    path_version: str | None
    archive_version: str | None

    @property
    def skewed(self) -> bool:
        """True when both paths exist and disagree about the version."""
        return (
            self.path_version is not None
            and self.archive_version is not None
            and self.path_version != self.archive_version
        )


def archive_version(archive: Path) -> str | None:
    """Return the version recorded inside a bundled archive, without running it."""
    try:
        with zipfile.ZipFile(archive) as bundle:
            source = bundle.read("shim_guard/__init__.py").decode("utf-8")
    except (OSError, KeyError, UnicodeDecodeError, zipfile.BadZipFile):
        return None
    found = _VERSION.search(source)
    return found.group("version") if found else None


def installed_plugin(home: Path | None = None) -> dict | None:
    """Return Claude Code's record of an installed SHIM plugin, if any.

    The record lives in an undocumented client file, so every failure mode here
    means "cannot tell", never "not installed".
    """
    root = Path(home) if home is not None else Path.home()
    manifest = root / ".claude" / "plugins" / "installed_plugins.json"
    try:
        if manifest.stat().st_size > MAX_MANIFEST_BYTES:
            return None
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    plugins = document.get("plugins")
    if not isinstance(plugins, dict):
        return None
    for key, entries in plugins.items():
        if key.split("@", 1)[0] != PLUGIN_NAME or not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict):
                return {"key": key, **entry}
    return None


def resolve(plugin_root: Path | None = None, which=shutil.which) -> Resolution:
    """Return the hook the launcher would run, mirroring its resolution order."""
    on_path = which("shim-guard-hook")
    root = plugin_root
    if root is None:
        configured = os.environ.get("CLAUDE_PLUGIN_ROOT")
        root = Path(configured) if configured else None
    archive = root / ARCHIVE_RELATIVE if root is not None else None
    bundled = archive if archive is not None and archive.is_file() else None
    bundled_version = archive_version(bundled) if bundled is not None else None

    from shim_guard import __version__

    if on_path is not None:
        return Resolution(
            "path",
            f"The package hook on PATH is active ({on_path}).",
            __version__,
            bundled_version,
        )
    if bundled is not None:
        return Resolution(
            "plugin",
            f"The archive bundled in the plugin is active ({bundled}).",
            None,
            bundled_version,
        )
    return Resolution(
        "none",
        "No hook is runnable; prompts are passing through uninspected.",
        None,
        None,
    )
