"""Pure installation and revert planning."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class StateKind(StrEnum):
    ABSENT = "absent"
    EXPECTED = "expected"
    OTHER = "other"
    UNSAFE = "unsafe"


class Action(StrEnum):
    CREATE = "create"
    REMOVE = "remove"
    NOOP = "noop"
    CONFLICT = "conflict"
    REFUSE = "refuse"


@dataclass(frozen=True, slots=True)
class FileState:
    kind: StateKind
    parent_device: int | None = None
    parent_inode: int | None = None
    device: int | None = None
    inode: int | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class Plan:
    operation: str
    target: Path
    expected: bytes
    state: FileState
    action: Action
    message: str
    guard_path: Path | None = None
    guard_state: os.stat_result | None = None

    @property
    def changes(self) -> bool:
        return self.action in {Action.CREATE, Action.REMOVE}


def plan_install(target: Path, expected: bytes, state: FileState) -> Plan:
    """Plan installation from an already-inspected filesystem state."""
    if state.kind is StateKind.ABSENT:
        action, message = Action.CREATE, "create the SHIM-owned Codex hook document"
    elif state.kind is StateKind.EXPECTED:
        action, message = Action.NOOP, "SHIM Guard is already installed"
    elif state.kind is StateKind.OTHER:
        action, message = (
            Action.CONFLICT,
            "existing hook configuration requires manual setup",
        )
    else:
        action, message = Action.REFUSE, state.reason or "unsafe installation target"
    return Plan("install", target, expected, state, action, message)


def plan_revert(target: Path, expected: bytes, state: FileState) -> Plan:
    """Plan exact revert from an already-inspected filesystem state."""
    if state.kind is StateKind.ABSENT:
        action, message = Action.NOOP, "SHIM Guard is not installed"
    elif state.kind is StateKind.EXPECTED:
        action, message = Action.REMOVE, "remove the exact SHIM-owned hook document"
    elif state.kind is StateKind.OTHER:
        action, message = (
            Action.CONFLICT,
            "hook configuration drifted; refusing to remove it",
        )
    else:
        action, message = Action.REFUSE, state.reason or "unsafe revert target"
    return Plan("revert", target, expected, state, action, message)
