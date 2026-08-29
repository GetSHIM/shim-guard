"""Local SHIM Guard entity policy."""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

try:  # pragma: no cover - exercised by whichever interpreter runs the tests
    import tomllib  # ty: ignore[unresolved-import]
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

ENTITY_TYPES = (
    "EMAIL",
    "PHONE",
    "CREDIT_CARD",
    "IBAN",
    "IP_ADDRESS",
    "MAC_ADDRESS",
    "US_SSN",
    "TR_NATIONAL_ID",
    "TR_VKN",
    "SECRET",
    "DB_URI",
)
DEFAULT_ENTITIES = ENTITY_TYPES
MAX_CONFIG_BYTES = 16_384


def config_path(home: Path | None = None) -> Path:
    """Return the user-scoped SHIM Guard settings path."""
    try:
        if home is not None:
            target = Path(home) / ".config" / "shim-guard" / "config.toml"
        elif configured := os.environ.get("SHIM_GUARD_CONFIG"):
            target = Path(configured).expanduser()
        elif configured := os.environ.get("XDG_CONFIG_HOME"):
            target = Path(configured).expanduser() / "shim-guard" / "config.toml"
        else:
            target = Path.home() / ".config" / "shim-guard" / "config.toml"
    except RuntimeError as error:
        raise ValueError("SHIM Guard settings path is invalid") from error
    return _validated_path(target)


def _validated_path(path: Path) -> Path:
    target = Path(path)
    if (
        not target.is_absolute()
        or ".." in target.parts
        or not str(target).isprintable()
    ):
        raise ValueError("SHIM Guard settings path is invalid")
    return target


def normalize_entities(entities: Iterable[object]) -> tuple[str, ...]:
    """Validate and return entities in the public display order."""
    values = tuple(entities)
    if any(not isinstance(value, str) for value in values):
        raise ValueError("entity names must be strings")
    selected = set(values)
    if len(values) != len(selected):
        raise ValueError("entity names must not be repeated")
    unknown = selected.difference(ENTITY_TYPES)
    if unknown:
        raise ValueError("unsupported entity name")
    return tuple(entity for entity in ENTITY_TYPES if entity in selected)


def render_settings(
    entities: Iterable[str],
    modes: dict | None = None,
    tool_entities: dict | None = None,
    ledger: bool = False,
) -> bytes:
    """Render the whole editable TOML settings document.

    Everything the file can hold is written, not just the part being changed.
    A writer that knows only about ``enabled_entities`` deletes the sections it
    has never heard of, which silently reverts a deliberate `enforce` back to
    the shipped default.
    """
    import tomli_w

    document: dict = {"enabled_entities": list(normalize_entities(entities))}
    if ledger:
        document["ledger"] = True
    # Tables must follow scalars in the mapping order tomli_w is given.
    if tool_entities:
        document["entities"] = {
            key: list(value) for key, value in sorted(tool_entities.items())
        }
    if modes:
        document["mode"] = dict(sorted(modes.items()))
    return tomli_w.dumps(document).encode()


def render_entities(entities: Iterable[str]) -> bytes:
    """Render a settings document holding only the entity list."""
    return render_settings(entities)


DEFAULT_MODES = {
    "user-prompt": "warn",
    "outbound": "enforce",
    "inbound": "enforce",
    "local-write": "warn",
    "executable-text": "warn",
}
MODES = ("observe", "warn", "enforce")
_TOP_LEVEL = {"enabled_entities", "mode", "entities", "ledger"}


@dataclass(frozen=True)
class Policy:
    """Enabled entities plus the mode to apply, by direction, event or tool."""

    entities: tuple
    modes: dict
    tool_entities: dict
    #: Whether decisions outlive the session. Off unless the user turns it on.
    ledger: bool = False

    def mode_for(self, direction: str, tool: str = "", event: str = "") -> str:
        """Return the mode for one payload, most specific override winning.

        Order: per-tool, then per-event, then per-direction, then the file's
        own default, then the shipped default for that direction.
        """
        for key in (tool, event, direction):
            if key and key in self.modes:
                return self.modes[key]
        if "default" in self.modes:
            return self.modes["default"]
        return DEFAULT_MODES.get(direction, "warn")

    def entities_for(self, tool: str = "", event: str = "") -> tuple:
        for key in (tool, event):
            if key and key in self.tool_entities:
                return self.tool_entities[key]
        return self.entities


def _modes(document: dict) -> dict:
    section = document.get("mode", {})
    if not isinstance(section, dict):
        raise ValueError("SHIM Guard settings are invalid")
    modes = {}
    for key, value in section.items():
        if not isinstance(value, str) or value not in MODES:
            raise ValueError("SHIM Guard settings are invalid")
        modes[key] = value
    return modes


def _tool_entities(document: dict) -> dict:
    section = document.get("entities", {})
    if not isinstance(section, dict):
        raise ValueError("SHIM Guard settings are invalid")
    scoped = {}
    for key, value in section.items():
        if not isinstance(value, list):
            raise ValueError("SHIM Guard settings are invalid")
        scoped[key] = normalize_entities(value)
    return scoped


def parse_settings(text: str) -> dict:
    """Parse and validate the settings document.

    A v1 file holding only ``enabled_entities`` is a valid v2 file; the extra
    sections are optional and inherit the shipped defaults when absent.
    """
    try:
        document = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, RecursionError) as error:
        raise ValueError("SHIM Guard settings are invalid") from error
    if not set(document) <= _TOP_LEVEL or "enabled_entities" not in document:
        raise ValueError("SHIM Guard settings are invalid")
    enabled = document["enabled_entities"]
    if not isinstance(enabled, list):
        raise ValueError("SHIM Guard settings are invalid")
    ledger = document.get("ledger", False)
    if not isinstance(ledger, bool):
        raise ValueError("SHIM Guard settings are invalid")
    return {
        "enabled_entities": list(enabled),
        "mode": _modes(document),
        "entities": _tool_entities(document),
        "ledger": ledger,
    }


def load_policy(path: Path | None = None) -> Policy:
    """Load the full policy, falling back to the shipped defaults."""
    target = config_path() if path is None else _validated_path(path)
    from shim_guard.installation import StateKind, inspect_file

    state = inspect_file(target, MAX_CONFIG_BYTES)
    if state.kind is StateKind.ABSENT:
        return Policy(DEFAULT_ENTITIES, {}, {})
    if state.kind is not StateKind.FILE or state.content is None:
        raise ValueError("SHIM Guard settings cannot be read safely")
    try:
        document = parse_settings(state.content.decode("utf-8"))
    except (UnicodeDecodeError, RecursionError) as error:
        raise ValueError("SHIM Guard settings are invalid") from error
    try:
        return Policy(
            normalize_entities(document["enabled_entities"]),
            document["mode"],
            document["entities"],
            document["ledger"],
        )
    except ValueError as error:
        raise ValueError("SHIM Guard settings are invalid") from error


def load_entities(path: Path | None = None) -> tuple[str, ...]:
    """Load enabled entities, using the default preset when no file exists."""
    target = config_path() if path is None else _validated_path(path)
    from shim_guard.installation import StateKind, inspect_file

    state = inspect_file(target, MAX_CONFIG_BYTES)
    if state.kind is StateKind.ABSENT:
        return DEFAULT_ENTITIES
    if state.kind is not StateKind.FILE or state.content is None:
        raise ValueError("SHIM Guard settings cannot be read safely")
    try:
        document = parse_settings(state.content.decode("utf-8"))
        return normalize_entities(document["enabled_entities"])
    except (UnicodeDecodeError, RecursionError, ValueError) as error:
        raise ValueError("SHIM Guard settings are invalid") from error
