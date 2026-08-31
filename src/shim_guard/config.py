"""Local SHIM Guard settings."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from shim_guard import policy
from shim_guard.guard import entities as entity_catalog

try:  # pragma: no cover - exercised by whichever interpreter runs the tests
    import tomllib  # ty: ignore[unresolved-import]
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

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


def render_settings(
    entities: Iterable[str],
    modes: dict | None = None,
    tool_entities: dict | None = None,
    ledger: bool = False,
    diet: tuple | None = None,
) -> bytes:
    """Render the whole editable TOML settings document.

    Everything the file can hold is written, not just the part being changed.
    A writer that knows only about ``enabled_entities`` deletes the sections it
    has never heard of, which silently reverts a deliberate `enforce` back to
    the shipped default.
    """
    import tomli_w

    document: dict = {
        "enabled_entities": list(entity_catalog.normalize_entities(entities))
    }
    if ledger:
        document["ledger"] = True
    if diet is not None:
        from shim_guard.events.diet import DEFAULT_TRANSFORMS

        if not diet:
            document["diet"] = False
        elif tuple(diet) != tuple(DEFAULT_TRANSFORMS):
            document["diet"] = list(diet)
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


_TOP_LEVEL = {"enabled_entities", "mode", "entities", "ledger", "diet"}


def _modes(document: dict) -> dict:
    section = document.get("mode", {})
    if not isinstance(section, dict):
        raise ValueError("SHIM Guard settings are invalid")
    modes = {}
    for key, value in section.items():
        if not isinstance(value, str) or value not in policy.MODES:
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
        scoped[key] = entity_catalog.normalize_entities(value)
    return scoped


def _diet(document: dict) -> tuple:
    """Return the enabled diet transforms.

    Absent means the shipped default. ``false`` turns diet off completely and
    a list names exactly which transforms run, which is R6's per-transform
    switch.
    """
    from shim_guard.events.diet import DEFAULT_TRANSFORMS, TRANSFORMS

    value = document.get("diet", True)
    if value is True:
        return DEFAULT_TRANSFORMS
    if value is False:
        return ()
    if not isinstance(value, list) or any(name not in TRANSFORMS for name in value):
        raise ValueError("SHIM Guard settings are invalid")
    return tuple(name for name in TRANSFORMS if name in value)


def parse_settings(text: str) -> dict:
    """Parse and validate the settings document.

    Every key is optional and inherits the shipped default when absent, so a
    v1 file holding only ``enabled_entities`` is a valid v2 file and a file
    holding only ``[mode]`` is too. `enabled_entities` used to be mandatory,
    which made a hand-written `[mode]` file fail to parse — and a settings file
    that will not parse fails closed on every prompt in the session.
    """
    try:
        document = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, RecursionError) as error:
        raise ValueError("SHIM Guard settings are invalid") from error
    if not set(document) <= _TOP_LEVEL:
        raise ValueError("SHIM Guard settings are invalid")
    enabled = document.get("enabled_entities", list(entity_catalog.DEFAULT_ENTITIES))
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
        "diet": _diet(document),
    }


def load_policy(path: Path | None = None) -> policy.Policy:
    """Load the full policy, falling back to the shipped defaults."""
    target = config_path() if path is None else _validated_path(path)
    from shim_guard.installation import StateKind, inspect_file

    state = inspect_file(target, MAX_CONFIG_BYTES)
    if state.kind is StateKind.ABSENT:
        # No file means the shipped defaults, which is not the same as the
        # dataclass defaults: `diet` ships on, and most users never write a
        # config file at all.
        from shim_guard.events.diet import DEFAULT_TRANSFORMS

        return policy.Policy(
            entity_catalog.DEFAULT_ENTITIES, {}, {}, False, DEFAULT_TRANSFORMS
        )
    if state.kind is not StateKind.FILE or state.content is None:
        raise ValueError("SHIM Guard settings cannot be read safely")
    try:
        document = parse_settings(state.content.decode("utf-8"))
    except (UnicodeDecodeError, RecursionError) as error:
        raise ValueError("SHIM Guard settings are invalid") from error
    try:
        return policy.Policy(
            entity_catalog.normalize_entities(document["enabled_entities"]),
            document["mode"],
            document["entities"],
            document["ledger"],
            document["diet"],
        )
    except ValueError as error:
        raise ValueError("SHIM Guard settings are invalid") from error


def load_entities(path: Path | None = None) -> tuple[str, ...]:
    """Load enabled entities, using the default preset when no file exists."""
    target = config_path() if path is None else _validated_path(path)
    from shim_guard.installation import StateKind, inspect_file

    state = inspect_file(target, MAX_CONFIG_BYTES)
    if state.kind is StateKind.ABSENT:
        return entity_catalog.DEFAULT_ENTITIES
    if state.kind is not StateKind.FILE or state.content is None:
        raise ValueError("SHIM Guard settings cannot be read safely")
    try:
        document = parse_settings(state.content.decode("utf-8"))
        return entity_catalog.normalize_entities(document["enabled_entities"])
    except (UnicodeDecodeError, RecursionError, ValueError) as error:
        raise ValueError("SHIM Guard settings are invalid") from error
