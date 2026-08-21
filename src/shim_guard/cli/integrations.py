"""Codex installation, inspection, and local compatibility workflows."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import typer

from shim_guard.cli.output import emit, emit_json


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    status: str
    detail: str


def require_codex(client: str) -> None:
    if client != "codex":
        emit("FAIL", "Unsupported client; only codex is supported.", error=True)
        raise typer.Exit(2)


def _codex_plan(operation: str):
    from shim_guard.clients.codex.settings import (
        MAX_CONFIG_BYTES,
        add_hook,
        remove_hook,
        target_path,
    )
    from shim_guard.installation.files import inspect_file
    from shim_guard.installation.plan import StateKind, plan_change

    target = target_path()
    state = inspect_file(target, MAX_CONFIG_BYTES)
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


def _inline_hooks_notice() -> None:
    from shim_guard.clients.codex.settings import has_inline_hooks

    try:
        inline_hooks = has_inline_hooks()
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


def _hook_fragment() -> str:
    from shim_guard.clients.codex.settings import hook_group

    return json.dumps(hook_group(), ensure_ascii=False, indent=2) + "\n"


def _plan_status(plan) -> tuple[str, str]:
    from shim_guard.installation.plan import Action

    if plan.action is Action.NOOP:
        return "PASS", "installed"
    if plan.action in {Action.CREATE, Action.UPDATE}:
        return "WARN", "not_installed"
    return "FAIL", "unsafe" if plan.action is Action.REFUSE else "conflict"


def _exit_for(label: str) -> None:
    if label == "WARN":
        raise typer.Exit(1)
    if label == "FAIL":
        raise typer.Exit(2)


def _plan_error(command: str, as_json: bool = False) -> None:
    if as_json:
        emit_json(command, "error", error="unable to inspect Codex hook configuration")
    else:
        emit("FAIL", "Unable to inspect Codex hook configuration.", error=True)
    raise typer.Exit(2)


def install(client: str, *, dry_run: bool, yes: bool) -> None:
    require_codex(client)
    try:
        plan = _codex_plan("install")
        from shim_guard.installation.plan import Action
    except (OSError, ValueError):
        _plan_error("install")

    if plan.action in {Action.CONFLICT, Action.REFUSE}:
        emit("FAIL", "Codex hook configuration cannot be changed safely.", error=True)
        emit(
            "WARN",
            "Review Codex hooks manually; SHIM did not change malformed, ambiguous, or unsafe settings.",
            error=True,
        )
        raise typer.Exit(2)
    if plan.action is Action.NOOP:
        emit("PASS", "SHIM Guard is already installed for Codex.")
        return
    if plan.action is Action.UPDATE:
        emit(
            "WARN",
            "Existing Codex hooks will be preserved; SHIM Guard will be appended last.",
        )
    _inline_hooks_notice()
    if dry_run:
        verb = "create" if plan.action is Action.CREATE else "append to"
        emit("WARN", f"Would {verb} Codex hooks at {plan.target} with this fragment:")
        print(_hook_fragment(), end="")
        return
    prompt = (
        "Create SHIM Guard's Codex hook?"
        if plan.action is Action.CREATE
        else "Append SHIM Guard after existing Codex hooks?"
    )
    if not yes and not typer.confirm(prompt, default=False):
        emit("WARN", "Installation cancelled.")
        raise typer.Exit(1)
    from shim_guard.installation.files import InstallationError, apply

    try:
        changed = apply(plan)
    except (InstallationError, OSError):
        emit("FAIL", "Codex hook configuration was not changed.", error=True)
        raise typer.Exit(2) from None
    emit(
        "PASS",
        (
            "Appended SHIM Guard after existing Codex hooks."
            if plan.action is Action.UPDATE
            else "Installed SHIM Guard for Codex."
        )
        if changed
        else "SHIM Guard is already installed for Codex.",
    )


def status(*, as_json: bool) -> None:
    try:
        plan = _codex_plan("install")
        label, state = _plan_status(plan)
    except (OSError, ValueError):
        _plan_error("status", as_json)
    if as_json:
        emit_json(
            "status", "ok" if label != "FAIL" else "error", client="codex", state=state
        )
    elif state == "installed":
        emit("PASS", "Codex hook configuration is installed.")
    elif state == "not_installed":
        emit("WARN", "Codex hook configuration is not installed.")
    else:
        emit(
            "FAIL",
            "Codex hook configuration is unsafe or differs from SHIM Guard.",
            error=True,
        )
    _exit_for(label)


def _codex_version() -> Check:
    from shim_guard.clients.codex.settings import (
        MINIMUM_CODEX_VERSION,
        TESTED_CODEX_VERSION,
    )

    path = shutil.which("codex")
    if path is None:
        return Check("codex", "FAIL", "Codex executable was not found on PATH.")
    try:
        result = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return Check("codex", "FAIL", f"Codex at {path} could not report its version.")
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", result.stdout + result.stderr)
    if result.returncode or match is None:
        return Check("codex", "FAIL", f"Codex at {path} has an unrecognized version.")
    version_text = match.group(0)
    version = tuple(int(part) for part in match.groups())
    minimum = tuple(int(part) for part in MINIMUM_CODEX_VERSION.split("."))
    tested = tuple(int(part) for part in TESTED_CODEX_VERSION.split("."))
    if version < minimum:
        return Check(
            "codex",
            "FAIL",
            f"Codex {version_text} is older than {MINIMUM_CODEX_VERSION}.",
        )
    if version > tested:
        return Check(
            "codex",
            "WARN",
            f"Codex {version_text} is newer than tested {TESTED_CODEX_VERSION}.",
        )
    return Check("codex", "PASS", f"Codex {version_text} at {path} is tested.")


def _hooks_feature() -> Check:
    path = shutil.which("codex")
    if path is None:
        return Check("hooks_feature", "FAIL", "Codex executable was not found on PATH.")
    try:
        result = subprocess.run(
            [path, "features", "list"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return Check(
            "hooks_feature", "FAIL", "Codex hook support could not be checked."
        )
    enabled = any(
        line.split()[:1] == ["hooks"] and line.split()[-1:] == ["true"]
        for line in result.stdout.splitlines()
    )
    if result.returncode or not enabled:
        return Check("hooks_feature", "FAIL", "Codex hook support is not enabled.")
    return Check("hooks_feature", "PASS", "Codex hook support is enabled.")


def _hook_state() -> Check:
    try:
        plan = _codex_plan("install")
        label, state = _plan_status(plan)
    except (OSError, ValueError):
        return Check(
            "hook_configuration", "FAIL", "Could not inspect Codex hook configuration."
        )
    messages = {
        "installed": "SHIM Guard's exact Codex matcher group is present.",
        "not_installed": "SHIM Guard's Codex matcher group is not installed.",
        "conflict": "Codex hook configuration needs manual review.",
        "unsafe": "Codex hook configuration cannot be trusted safely.",
    }
    return Check("hook_configuration", label, messages[state])


def _entity_settings() -> Check:
    from shim_guard.config import ENTITY_TYPES, load_entities

    try:
        enabled = load_entities()
    except (OSError, ValueError):
        return Check(
            "entity_settings",
            "FAIL",
            "Entity settings are unsafe or invalid; reset malformed contents or review the path.",
        )
    if not enabled:
        return Check(
            "entity_settings",
            "WARN",
            "All sensitive-data detection is disabled; review with `shim config`.",
        )
    return Check(
        "entity_settings",
        "PASS",
        f"{len(enabled)}/{len(ENTITY_TYPES)} sensitive-data entities are enabled.",
    )


def _runner_check() -> Check:
    from shim_guard.clients.codex.settings import HOOK_TIMEOUT_SECONDS

    command = [sys.executable, "-I", "-B", "-m", "shim_guard.hook"]
    safe = json.dumps(
        {"hook_event_name": "UserPromptSubmit", "prompt": "Synthetic safe prompt"}
    )
    blocked = json.dumps(
        {"hook_event_name": "UserPromptSubmit", "prompt": "email demo@example.com"}
    )
    try:
        with tempfile.TemporaryDirectory(prefix="shim-guard-doctor-") as directory:
            environment = os.environ.copy()
            environment["SHIM_GUARD_CONFIG"] = str(
                Path(directory).resolve() / "config.toml"
            )
            safe_result = subprocess.run(
                command,
                input=safe,
                text=True,
                capture_output=True,
                timeout=HOOK_TIMEOUT_SECONDS + 5,
                check=False,
                env=environment,
            )
            block_result = subprocess.run(
                command,
                input=blocked,
                text=True,
                capture_output=True,
                timeout=HOOK_TIMEOUT_SECONDS + 5,
                check=False,
                env=environment,
            )
        block = json.loads(block_result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return Check(
            "runner", "FAIL", "The local hook runner fixtures did not complete."
        )
    if safe_result.returncode or safe_result.stdout or safe_result.stderr:
        return Check(
            "runner",
            "FAIL",
            "The local hook runner did not allow the safe fixture silently.",
        )
    if (
        block_result.returncode
        or block_result.stderr
        or not isinstance(block, dict)
        or block.get("decision") != "block"
    ):
        return Check(
            "runner",
            "FAIL",
            "The local hook runner did not block the sensitive fixture.",
        )
    return Check(
        "runner",
        "PASS",
        "Local hook runner allowed and blocked direct fixtures correctly.",
    )


def _activation_check() -> Check:
    return Check(
        "hook_activation",
        "WARN",
        "Codex trust activation is client UI state; verify SHIM with /hooks.",
    )


def doctor(client: str, *, as_json: bool) -> None:
    require_codex(client)
    checks = (
        _codex_version(),
        _hooks_feature(),
        _hook_state(),
        _entity_settings(),
        _runner_check(),
        _activation_check(),
    )
    if as_json:
        status = (
            "error"
            if any(check.status == "FAIL" for check in checks)
            else "warning"
            if any(check.status == "WARN" for check in checks)
            else "ok"
        )
        emit_json(
            "doctor",
            status,
            client="codex",
            checks=[{"name": check.name, "status": check.status} for check in checks],
        )
    else:
        for check in checks:
            emit(check.status, check.detail, error=check.status == "FAIL")
    if any(check.status == "FAIL" for check in checks):
        raise typer.Exit(2)
    if any(check.status == "WARN" for check in checks):
        raise typer.Exit(1)


def revert(client: str, *, yes: bool) -> None:
    require_codex(client)
    try:
        plan = _codex_plan("revert")
        from shim_guard.installation.plan import Action
    except (OSError, ValueError):
        _plan_error("revert")
    if plan.action in {Action.CONFLICT, Action.REFUSE}:
        emit("FAIL", "Codex hook configuration cannot be removed safely.", error=True)
        emit(
            "WARN",
            "Review Codex hooks manually; SHIM removes only its exact matcher group.",
            error=True,
        )
        raise typer.Exit(2)
    if plan.action is Action.NOOP:
        emit("PASS", "SHIM Guard is not installed for Codex.")
        return
    emit(
        "WARN",
        "Only SHIM Guard's exact matcher group will be removed; other hooks will be preserved.",
    )
    if not yes and not typer.confirm("Remove SHIM Guard's Codex hook?", default=False):
        emit("WARN", "Revert cancelled.")
        raise typer.Exit(1)
    from shim_guard.installation.files import InstallationError, apply

    try:
        changed = apply(plan)
    except (InstallationError, OSError):
        emit("FAIL", "Codex hook configuration was not changed.", error=True)
        raise typer.Exit(2) from None
    emit(
        "PASS",
        "Removed SHIM Guard and preserved the Codex hook document."
        if changed
        else "SHIM Guard is not installed for Codex.",
    )
