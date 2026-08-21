from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from shim_guard.installation import (
    Action,
    FileState,
    InstallationError,
    StateKind,
    apply,
    inspect_install,
    inspect_revert,
    plan_install,
    plan_revert,
)

EXPECTED = b'{"shim":"guard"}\n'


def target_in(tmp_path: Path) -> Path:
    parent = tmp_path / ".codex"
    parent.mkdir(mode=0o700)
    return parent / "hooks.json"


def test_pure_planning_has_no_filesystem_effect(tmp_path: Path) -> None:
    target = tmp_path / "missing" / "hooks.json"
    absent = FileState(StateKind.ABSENT)
    assert plan_install(target, EXPECTED, absent).action is Action.CREATE
    assert plan_revert(target, EXPECTED, absent).action is Action.NOOP
    assert not target.parent.exists()


def test_dry_inspection_has_zero_writes(tmp_path: Path) -> None:
    target = target_in(tmp_path)
    before = set(target.parent.iterdir())
    plan = inspect_install(target, EXPECTED)
    assert plan.action is Action.CREATE
    assert set(target.parent.iterdir()) == before


def test_install_is_exact_mode_0600_and_idempotent(tmp_path: Path) -> None:
    target = target_in(tmp_path)
    assert apply(inspect_install(target, EXPECTED))
    assert target.read_bytes() == EXPECTED
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert list(target.parent.iterdir()) == [target]
    assert not apply(inspect_install(target, EXPECTED))


def test_existing_content_is_a_manual_conflict(tmp_path: Path) -> None:
    target = target_in(tmp_path)
    target.write_bytes(b'{"hooks":{"shared":true}}\n')
    before = target.read_bytes()
    plan = inspect_install(target, EXPECTED)
    assert plan.action is Action.CONFLICT
    with pytest.raises(InstallationError):
        apply(plan)
    assert target.read_bytes() == before


def test_exact_revert_and_absent_revert_are_safe(tmp_path: Path) -> None:
    target = target_in(tmp_path)
    apply(inspect_install(target, EXPECTED))
    assert apply(inspect_revert(target, EXPECTED))
    assert not target.exists()
    assert not apply(inspect_revert(target, EXPECTED))


def test_revert_refuses_content_drift(tmp_path: Path) -> None:
    target = target_in(tmp_path)
    apply(inspect_install(target, EXPECTED))
    plan = inspect_revert(target, EXPECTED)
    target.write_bytes(b"drift")
    with pytest.raises(InstallationError):
        apply(plan)
    assert target.read_bytes() == b"drift"


def test_revert_refuses_same_content_on_a_new_inode(tmp_path: Path) -> None:
    target = target_in(tmp_path)
    apply(inspect_install(target, EXPECTED))
    plan = inspect_revert(target, EXPECTED)
    replacement = target.with_name("replacement")
    replacement.write_bytes(EXPECTED)
    os.replace(replacement, target)
    with pytest.raises(InstallationError, match="inode"):
        apply(plan)
    assert target.read_bytes() == EXPECTED


def test_stale_install_plans_converge_without_overwriting(tmp_path: Path) -> None:
    target = target_in(tmp_path)
    first = inspect_install(target, EXPECTED)
    second = inspect_install(target, EXPECTED)
    assert apply(first)
    assert not apply(second)
    assert target.read_bytes() == EXPECTED


def test_install_refuses_file_appearing_after_plan(tmp_path: Path) -> None:
    target = target_in(tmp_path)
    plan = inspect_install(target, EXPECTED)
    target.write_bytes(b"other hook")
    with pytest.raises(InstallationError):
        apply(plan)
    assert target.read_bytes() == b"other hook"


def test_symlink_target_and_parent_are_refused(tmp_path: Path) -> None:
    target = target_in(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_bytes(EXPECTED)
    target.symlink_to(elsewhere)
    assert inspect_install(target, EXPECTED).action is Action.REFUSE

    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    plan = inspect_install(linked_parent / "hooks.json", EXPECTED)
    assert plan.action is Action.REFUSE


def test_hardlinked_target_is_never_treated_as_exclusively_owned(
    tmp_path: Path,
) -> None:
    target = target_in(tmp_path)
    shared = tmp_path / "shared-hooks.json"
    shared.write_bytes(EXPECTED)
    os.link(shared, target)

    assert inspect_install(target, EXPECTED).action is Action.REFUSE
    assert inspect_revert(target, EXPECTED).action is Action.REFUSE
    assert shared.read_bytes() == EXPECTED


def test_revert_refuses_a_hardlink_added_during_locked_revalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = target_in(tmp_path)
    apply(inspect_install(target, EXPECTED))
    plan = inspect_revert(target, EXPECTED)
    shared = tmp_path / "shared-hooks.json"
    real_stat = os.stat
    target_stats = 0

    def add_link_before_final_stat(path, *args, **kwargs):
        nonlocal target_stats
        if path == target.name and kwargs.get("dir_fd") is not None:
            target_stats += 1
            if target_stats == 2:
                os.link(target, shared)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", add_link_before_final_stat)
    with pytest.raises(InstallationError, match="changed before removal"):
        apply(plan)
    assert target.exists()
    assert shared.read_bytes() == EXPECTED


def test_nonregular_target_is_refused(tmp_path: Path) -> None:
    target = target_in(tmp_path)
    target.mkdir()
    assert inspect_install(target, EXPECTED).action is Action.REFUSE


def test_unsafe_parent_and_target_permissions_are_refused(tmp_path: Path) -> None:
    target = target_in(tmp_path)
    target.parent.chmod(0o770)
    assert inspect_install(target, EXPECTED).action is Action.REFUSE

    target.parent.chmod(0o700)
    target.write_bytes(EXPECTED)
    target.chmod(0o620)
    assert inspect_revert(target, EXPECTED).action is Action.REFUSE


def test_unowned_parent_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = target_in(tmp_path)
    monkeypatch.setattr(os, "geteuid", lambda: target.parent.stat().st_uid + 1)
    assert inspect_install(target, EXPECTED).action is Action.REFUSE


def test_relative_and_overlong_targets_are_refused(tmp_path: Path) -> None:
    assert inspect_install(Path("hooks.json"), EXPECTED).action is Action.REFUSE
    unsafe_parent = tmp_path / "unsafe\x1b-parent"
    unsafe_parent.mkdir()
    assert (
        inspect_install(unsafe_parent / "hooks.json", EXPECTED).action is Action.REFUSE
    )
    assert (
        inspect_install(tmp_path / "missing" / "hooks.json", EXPECTED).action
        is Action.REFUSE
    )
    target = tmp_path / ("x" * 250) / ("y" * 250)
    # Keep this independent of the host's lower per-component path limit.
    while len(os.fsencode(target)) <= 4_096:
        target /= "z" * 250
    assert inspect_install(target, EXPECTED).action is Action.REFUSE
