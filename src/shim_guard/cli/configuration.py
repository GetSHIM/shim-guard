"""User-facing entity policy workflow."""

from __future__ import annotations

import sys
from typing import Never

import typer
from rich import box
from rich.table import Table
from rich.text import Text

from shim_guard.cli.output import console, emit, emit_json, terminal_text
from shim_guard.config import (
    DEFAULT_ENTITIES,
    ENTITY_TYPES,
    MAX_CONFIG_BYTES,
    config_path,
    load_entities,
    normalize_entities,
    render_entities,
)
from shim_guard.installation import InstallationError, apply, inspect_file, plan_change


def _show(enabled: tuple[str, ...], title: str) -> None:
    selected = set(enabled)
    output = console()
    count = f"{len(enabled)}/{len(ENTITY_TYPES)}"
    heading = (
        f"Entities: {count} on" if output.width < 30 else f"{title}: {count} enabled"
    )
    output.print(Text(heading, style="bold"))
    if output.width < 30:
        for entity in ENTITY_TYPES:
            state = "ON " if entity in selected else "OFF "
            output.print(
                Text(state + entity, style="green" if entity in selected else "dim")
            )
    else:
        table = Table(box=box.SIMPLE, pad_edge=False)
        table.add_column("Entity", overflow="fold")
        table.add_column("Status", no_wrap=True)
        for entity in ENTITY_TYPES:
            status = (
                Text("ON", style="green")
                if entity in selected
                else Text("OFF", style="dim")
            )
            table.add_row(entity, status)
        output.print(table)
    if not enabled:
        emit("WARN", "All sensitive-data detection is disabled.")


def _fail(as_json: bool, message: str = "Unable to update entity settings.") -> Never:
    if as_json:
        emit_json("config", "error", error="unable to process entity settings")
    else:
        emit("FAIL", message, error=True)
    raise typer.Exit(2)


def _emit_settings_json(enabled: tuple[str, ...], **data: object) -> None:
    selected = set(enabled)
    emit_json(
        "config",
        "ok",
        **data,
        enabled_entities=list(enabled),
        disabled_entities=[entity for entity in ENTITY_TYPES if entity not in selected],
    )


def configure(
    *,
    only: tuple[str, ...],
    enable: tuple[str, ...],
    disable: tuple[str, ...],
    reset: bool,
    yes: bool,
    as_json: bool,
) -> None:
    """Show or safely update the enabled entity types."""
    try:
        target = config_path()
    except ValueError:
        _fail(as_json, "Entity settings path is invalid.")
    changing = bool(only or enable or disable or reset)
    if reset and (only or enable or disable):
        _fail(as_json, "--reset cannot be combined with entity options.")
    if only and (enable or disable):
        _fail(as_json, "--only cannot be combined with --enable or --disable.")
    if set(enable).intersection(disable):
        _fail(as_json, "The same entity cannot be enabled and disabled.")

    try:
        if reset:
            enabled = DEFAULT_ENTITIES
        elif only:
            enabled = normalize_entities(set(only))
        else:
            selected = set(load_entities(target))
            selected.update(enable)
            selected.difference_update(disable)
            enabled = normalize_entities(selected)
    except (OSError, ValueError):
        _fail(
            as_json,
            "Entity settings are invalid or unsafe. Reset malformed contents; review unsafe paths manually.",
        )

    if not changing:
        if as_json:
            _emit_settings_json(enabled)
            return
        _show(enabled, "Current detection")
        emit("PASS", f"File: {terminal_text(str(target), sys.stdout)}")
        return

    if as_json and not yes:
        _fail(True)
    if not as_json:
        _show(enabled, "New detection")
        emit("WARN", f"File: {terminal_text(str(target), sys.stdout)}")
        if not yes and not typer.confirm("Save these settings?", default=False):
            emit("WARN", "Settings unchanged.")
            raise typer.Exit(1)

    try:
        target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        state = inspect_file(target, MAX_CONFIG_BYTES)
        changed = apply(plan_change(target, state, render_entities(enabled)))
    except (InstallationError, OSError, ValueError):
        _fail(as_json, "Entity settings were unsafe or changed; nothing was saved.")

    if as_json:
        _emit_settings_json(enabled, changed=changed)
    else:
        emit(
            "PASS",
            "Entity settings saved." if changed else "Entity settings already match.",
        )
