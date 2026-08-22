"""Local SHIM Guard entity policy."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Iterable
from pathlib import Path

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


def render_entities(entities: Iterable[str]) -> bytes:
    """Render the small editable TOML settings document."""
    import tomli_w

    document = {"enabled_entities": list(normalize_entities(entities))}
    return tomli_w.dumps(document).encode()


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
        document = tomllib.loads(state.content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError) as error:
        raise ValueError("SHIM Guard settings are invalid") from error
    if set(document) != {"enabled_entities"}:
        raise ValueError("SHIM Guard settings are invalid")
    enabled = document["enabled_entities"]
    if not isinstance(enabled, list):
        raise ValueError("SHIM Guard settings are invalid")
    try:
        return normalize_entities(enabled)
    except ValueError as error:
        raise ValueError("SHIM Guard settings are invalid") from error
