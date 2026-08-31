"""Client hook installation, inspection, and revert workflows."""

from __future__ import annotations

import json
from typing import Literal, NoReturn

import typer

from shim_guard.cli.output import emit, emit_json
from shim_guard.clients.claude import settings as claude_settings
from shim_guard.clients.codex import settings as codex_settings
from shim_guard.clients.copilot import settings as copilot_settings
from shim_guard.settings_files import (
    Action,
    InstallationError,
    Plan,
    StateKind,
    apply,
    ensure_parent,
    inspect_file,
    plan_change,
)


def client_name(client: str) -> str:
    if client == "claude":
        return "Claude Code"
    if client == "codex":
        return "Codex"
    if client == "copilot":
        return "GitHub Copilot CLI"
    raise ValueError("unsupported client")


def client_plan(client: str, operation: Literal["install", "revert"]) -> Plan:
    if client == "claude":
        target = claude_settings.target_path()
        limit = claude_settings.MAX_CONFIG_BYTES
        add_hook = claude_settings.add_hook
        remove_hook = claude_settings.remove_hook
    elif client == "codex":
        target = codex_settings.target_path()
        limit = codex_settings.MAX_CONFIG_BYTES
        add_hook = codex_settings.add_hook
        remove_hook = codex_settings.remove_hook
    elif client == "copilot":
        target = copilot_settings.target_path()
        limit = copilot_settings.MAX_CONFIG_BYTES
        add_hook = copilot_settings.add_hook
        remove_hook = copilot_settings.remove_hook
    else:
        raise ValueError("unsupported client")
    state = inspect_file(target, limit)
    if state.kind is StateKind.UNSAFE:
        return plan_change(target, state, None)
    if state.kind is StateKind.ABSENT:
        expected = add_hook(None) if operation == "install" else None
        return plan_change(target, state, expected)
    assert state.content is not None
    try:
        expected = (
            add_hook(state.content)
            if operation == "install"
            else remove_hook(state.content)
        )
    except ValueError as error:
        return plan_change(target, state, state.content, conflict=str(error))
    return plan_change(target, state, expected)


def _hook_fragment(client: str) -> dict[str, object]:
    """Return exactly what `--dry-run` would merge into the client's file."""
    if client == "copilot":
        return copilot_settings.hook_document()
    if client == "claude":
        registrations = claude_settings.hook_groups()
    elif client == "codex":
        registrations = codex_settings.hook_groups()
    else:
        raise ValueError("unsupported client")
    hooks: dict[str, list] = {}
    for event, group in registrations:
        hooks.setdefault(event, []).append(group)
    return {"hooks": hooks}


def _inline_hooks_notice(client: str) -> None:
    if client != "codex":
        return
    try:
        inline_hooks = codex_settings.has_inline_hooks()
    except ValueError:
        emit(
            "WARN", "Codex config.toml could not be inspected and will stay untouched."
        )
        return
    if inline_hooks:
        emit(
            "WARN",
            "Inline config.toml hooks will stay untouched and may coexist with hooks.json.",
        )


def plan_status(plan: Plan) -> tuple[str, str]:
    if plan.action is Action.NOOP:
        return "PASS", "installed"
    if plan.state.kind is StateKind.ABSENT or plan.action in {
        Action.CREATE,
        Action.UPDATE,
    }:
        return "WARN", "not_installed"
    return "FAIL", "unsafe" if plan.action is Action.REFUSE else "conflict"


def _plan_error(client: str, command: str, as_json: bool = False) -> NoReturn:
    name = client_name(client)
    if as_json:
        emit_json(
            command,
            "error",
            client=client,
            error=f"unable to inspect {name} hook configuration",
        )
    else:
        emit("FAIL", f"Unable to inspect {name} hook configuration.", error=True)
    raise typer.Exit(2)


def install(*, client: str, dry_run: bool, yes: bool) -> None:
    name = client_name(client)
    try:
        plan = client_plan(client, "install")
    except (OSError, ValueError):
        _plan_error(client, "install")

    # A config directory that does not exist yet is the first-run case, not an
    # unsafe one, and it is the same for every client: someone who installs
    # shim before ever launching the client has no `~/.claude`, `~/.codex` or
    # `~/.copilot`. This used to be recognised for Copilot alone, so the other
    # two refused with "cannot be changed safely — review malformed, ambiguous,
    # or unsafe settings" about settings that did not exist.
    missing_parent = (
        plan.action is Action.REFUSE
        and plan.state.kind is StateKind.ABSENT
        and not plan.target.parent.exists()
    )
    action = (
        Action.CREATE
        if missing_parent or (client == "copilot" and plan.action is Action.UPDATE)
        else plan.action
    )
    if action in {Action.CONFLICT, Action.REFUSE}:
        emit("FAIL", f"{name} hook configuration cannot be changed safely.", error=True)
        emit(
            "WARN",
            f"Review {name} hooks manually; SHIM did not change malformed, ambiguous, or unsafe settings.",
            error=True,
        )
        raise typer.Exit(2)
    if action is Action.NOOP:
        emit("PASS", f"SHIM Guard is already installed for {name}.")
        return
    if action is Action.UPDATE:
        emit(
            "WARN",
            f"Existing {name} hooks will be preserved; SHIM Guard will be appended last.",
        )
    _inline_hooks_notice(client)
    if dry_run:
        verb = "create" if action is Action.CREATE else "append to"
        emit("WARN", f"Would {verb} {name} hooks at {plan.target} with this fragment:")
        print(json.dumps(_hook_fragment(client), ensure_ascii=False, indent=2))
        return
    prompt = (
        f"Create SHIM Guard's {name} hook?"
        if action is Action.CREATE
        else f"Append SHIM Guard after existing {name} hooks?"
    )
    if not yes and not typer.confirm(prompt, default=False):
        emit("WARN", "Installation cancelled.")
        raise typer.Exit(1)
    if missing_parent:
        try:
            ensure_parent(plan.target)
            plan = client_plan(client, "install")
        except (InstallationError, OSError, ValueError):
            emit("FAIL", f"{name} hook configuration was not changed.", error=True)
            raise typer.Exit(2) from None
        if plan.action is Action.NOOP:
            emit("PASS", f"SHIM Guard is already installed for {name}.")
            return
        if plan.action is not Action.CREATE:
            emit(
                "FAIL", f"{name} hook configuration changed before install.", error=True
            )
            raise typer.Exit(2)
    try:
        from shim_guard.guard import evaluate

        evaluate("Synthetic safe prompt")
    except Exception:
        emit("FAIL", "SHIM Guard detector could not start.", error=True)
        raise typer.Exit(2) from None
    try:
        apply(plan)
    except (InstallationError, OSError):
        emit("FAIL", f"{name} hook configuration was not changed.", error=True)
        raise typer.Exit(2) from None
    emit(
        "PASS",
        f"Appended SHIM Guard after existing {name} hooks."
        if action is Action.UPDATE
        else f"Installed SHIM Guard for {name}.",
    )


def status(*, client: str, as_json: bool) -> None:
    name = client_name(client)
    try:
        plan = client_plan(client, "install")
        label, state = plan_status(plan)
    except (OSError, ValueError):
        _plan_error(client, "status", as_json)
    if as_json:
        emit_json(
            "status", "ok" if label != "FAIL" else "error", client=client, state=state
        )
    elif state == "installed":
        emit("PASS", f"{name} hook configuration is installed.")
    elif state == "not_installed":
        emit("WARN", f"{name} hook configuration is not installed.")
    else:
        emit(
            "FAIL",
            f"{name} hook configuration is unsafe or differs from SHIM Guard.",
            error=True,
        )
    if label == "WARN":
        raise typer.Exit(1)
    if label == "FAIL":
        raise typer.Exit(2)


def revert(*, client: str, yes: bool) -> None:
    name = client_name(client)
    try:
        plan = client_plan(client, "revert")
    except (OSError, ValueError):
        _plan_error(client, "revert")
    if plan.action in {Action.CONFLICT, Action.REFUSE}:
        emit("FAIL", f"{name} hook configuration cannot be removed safely.", error=True)
        emit(
            "WARN",
            f"Review {name} hooks manually; SHIM removes only its exact hook group.",
            error=True,
        )
        raise typer.Exit(2)
    if plan.action is Action.NOOP:
        emit("PASS", f"SHIM Guard is not installed for {name}.")
        return
    emit(
        "WARN",
        "Only SHIM Guard's exact hook group will be removed; other hooks will be preserved.",
    )
    if not yes and not typer.confirm(
        f"Remove SHIM Guard's {name} hook?", default=False
    ):
        emit("WARN", "Revert cancelled.")
        raise typer.Exit(1)
    try:
        apply(plan)
    except (InstallationError, OSError):
        emit("FAIL", f"{name} hook configuration was not changed.", error=True)
        raise typer.Exit(2) from None
    emit("PASS", f"Removed SHIM Guard and preserved the {name} settings file.")
