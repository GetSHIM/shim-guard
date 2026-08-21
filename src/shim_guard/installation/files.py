"""Single safe filesystem boundary for installation and exact revert."""

from __future__ import annotations

import fcntl
import os
import secrets
import stat
from pathlib import Path

from .plan import Action, FileState, Plan, StateKind, plan_install, plan_revert

MAX_TARGET_BYTES = 4_096
_UNSAFE_WRITABLE = stat.S_IWGRP | stat.S_IWOTH


class InstallationError(RuntimeError):
    """A configuration change was unsafe or no longer matched its plan."""


def _validate_target(target: Path) -> None:
    if (
        not target.is_absolute()
        or ".." in target.parts
        or not str(target).isprintable()
    ):
        raise InstallationError(
            "installation target must be an absolute normalized path"
        )
    if len(os.fsencode(target)) > MAX_TARGET_BYTES or not target.name:
        raise InstallationError("installation target is too long")


def _safe_owner_mode(info: os.stat_result, label: str) -> str:
    if info.st_uid != os.geteuid():
        return f"{label} is not owned by the current user"
    if info.st_mode & _UNSAFE_WRITABLE:
        return f"{label} is writable by another user"
    return ""


def _parent_state(target: Path) -> tuple[os.stat_result | None, str]:
    try:
        info = target.parent.lstat()
    except OSError as error:
        return None, f"cannot inspect target parent: {error.strerror}"
    if stat.S_ISLNK(info.st_mode):
        return None, "target parent must not be a symlink"
    if not stat.S_ISDIR(info.st_mode):
        return None, "target parent must be a directory"
    return (info, _safe_owner_mode(info, "target parent"))


def _read_expected_file(
    target: Path, expected: bytes, parent_fd: int | None = None
) -> FileState:
    if parent_fd is None:
        parent, reason = _parent_state(target)
        if parent is None or reason:
            return FileState(StateKind.UNSAFE, reason=reason)
    else:
        try:
            parent = os.fstat(parent_fd)
        except OSError as error:
            return FileState(
                StateKind.UNSAFE,
                reason=f"cannot inspect target parent: {error.strerror}",
            )
    try:
        info = (
            target.lstat()
            if parent_fd is None
            else os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
        )
    except FileNotFoundError:
        return FileState(StateKind.ABSENT, parent.st_dev, parent.st_ino)
    except OSError as error:
        return FileState(
            StateKind.UNSAFE,
            parent.st_dev,
            parent.st_ino,
            reason=f"cannot inspect target: {error.strerror}",
        )
    if stat.S_ISLNK(info.st_mode):
        return FileState(
            StateKind.UNSAFE,
            parent.st_dev,
            parent.st_ino,
            reason="target must not be a symlink",
        )
    if not stat.S_ISREG(info.st_mode):
        return FileState(
            StateKind.UNSAFE,
            parent.st_dev,
            parent.st_ino,
            reason="target must be a regular file",
        )
    if info.st_nlink != 1:
        return FileState(
            StateKind.UNSAFE,
            parent.st_dev,
            parent.st_ino,
            reason="target must not be hard-linked",
        )
    reason = _safe_owner_mode(info, "target")
    if reason:
        return FileState(StateKind.UNSAFE, parent.st_dev, parent.st_ino, reason=reason)
    if info.st_size != len(expected):
        return FileState(
            StateKind.OTHER, parent.st_dev, parent.st_ino, info.st_dev, info.st_ino
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(
            target if parent_fd is None else target.name,
            flags,
            dir_fd=parent_fd,
        )
        try:
            opened = os.fstat(descriptor)
            parts: list[bytes] = []
            remaining = len(expected) + 1
            while remaining:
                part = os.read(descriptor, remaining)
                if not part:
                    break
                parts.append(part)
                remaining -= len(part)
            content = b"".join(parts)
            closed = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        return FileState(
            StateKind.UNSAFE,
            parent.st_dev,
            parent.st_ino,
            reason=f"cannot safely read target: {error.strerror}",
        )
    if (
        (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
        or opened.st_nlink != 1
        or closed.st_nlink != 1
        or (closed.st_size, closed.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns)
    ):
        return FileState(
            StateKind.UNSAFE,
            parent.st_dev,
            parent.st_ino,
            info.st_dev,
            info.st_ino,
            "target changed during inspection",
        )
    kind = StateKind.EXPECTED if content == expected else StateKind.OTHER
    return FileState(kind, parent.st_dev, parent.st_ino, info.st_dev, info.st_ino)


def _inspect(target: Path, expected: bytes, operation: str) -> Plan:
    try:
        _validate_target(target)
    except InstallationError as error:
        state = FileState(StateKind.UNSAFE, reason=str(error))
    else:
        state = _read_expected_file(target, expected)
    planner = plan_install if operation == "install" else plan_revert
    return planner(target, expected, state)


def inspect_install(target: Path, expected: bytes) -> Plan:
    """Read configuration state without writing and return an install plan."""
    return _inspect(Path(target), expected, "install")


def inspect_revert(target: Path, expected: bytes) -> Plan:
    """Read configuration state without writing and return a revert plan."""
    return _inspect(Path(target), expected, "revert")


def _open_locked_parent(plan: Plan) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(plan.target.parent, flags)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        info = os.fstat(descriptor)
    except OSError as error:
        if "descriptor" in locals():
            os.close(descriptor)
        raise InstallationError(
            f"cannot lock target parent: {error.strerror}"
        ) from error
    if (info.st_dev, info.st_ino) != (
        plan.state.parent_device,
        plan.state.parent_inode,
    ):
        os.close(descriptor)
        raise InstallationError("target parent changed after planning")
    reason = _safe_owner_mode(info, "target parent")
    if reason:
        os.close(descriptor)
        raise InstallationError(reason)
    return descriptor


def _stat_fingerprint(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_uid,
        info.st_gid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _guard_unchanged(plan: Plan, parent_fd: int) -> None:
    if plan.guard_path is None:
        return
    try:
        _validate_target(plan.guard_path)
    except InstallationError as error:
        raise InstallationError("guarded configuration path is unsafe") from error
    if plan.guard_path.parent != plan.target.parent:
        raise InstallationError("guarded configuration must share the target parent")
    try:
        current = os.stat(
            plan.guard_path.name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        if plan.guard_state is None:
            return
    except OSError as error:
        raise InstallationError(
            "guarded configuration cannot be revalidated"
        ) from error
    else:
        if plan.guard_state is not None and _stat_fingerprint(
            current
        ) == _stat_fingerprint(plan.guard_state):
            return
    raise InstallationError("guarded configuration changed after planning")


def _publish(plan: Plan, parent_fd: int) -> bool:
    current = _read_expected_file(plan.target, plan.expected, parent_fd)
    if (current.parent_device, current.parent_inode) != (
        plan.state.parent_device,
        plan.state.parent_inode,
    ):
        raise InstallationError("target parent changed after locking")
    if current.kind is StateKind.EXPECTED:
        return False
    if current.kind is not StateKind.ABSENT:
        raise InstallationError(
            current.reason or "installation target changed after planning"
        )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for _attempt in range(10):
        temporary = f".{plan.target.name}.shim-{secrets.token_hex(8)}"
        try:
            descriptor = os.open(temporary, flags, 0o600, dir_fd=parent_fd)
            break
        except FileExistsError:
            continue
    else:
        raise InstallationError("could not allocate a safe temporary hook document")
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(plan.expected)
        while view:
            view = view[os.write(descriptor, view) :]
        os.fsync(descriptor)
        written = os.fstat(descriptor)
        temporary_info = os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)
        if (temporary_info.st_dev, temporary_info.st_ino) != (
            written.st_dev,
            written.st_ino,
        ):
            raise InstallationError(
                "temporary hook document changed before publication"
            )
        _guard_unchanged(plan, parent_fd)
        try:
            os.link(
                temporary,
                plan.target.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileExistsError as error:
            raise InstallationError(
                "installation target appeared during publication"
            ) from error
        published = os.stat(plan.target.name, dir_fd=parent_fd, follow_symlinks=False)
        if (published.st_dev, published.st_ino) != (written.st_dev, written.st_ino):
            raise InstallationError("hook document changed during publication")
        os.fsync(parent_fd)
        return True
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass


def _remove(plan: Plan, parent_fd: int) -> bool:
    current = _read_expected_file(plan.target, plan.expected, parent_fd)
    if (current.parent_device, current.parent_inode) != (
        plan.state.parent_device,
        plan.state.parent_inode,
    ):
        raise InstallationError("target parent changed after locking")
    if current.kind is StateKind.ABSENT:
        return False
    if current.kind is not StateKind.EXPECTED:
        raise InstallationError(
            current.reason or "hook configuration drifted after planning"
        )
    if (current.device, current.inode) != (plan.state.device, plan.state.inode):
        raise InstallationError("hook document inode changed after planning")
    try:
        latest = os.stat(plan.target.name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as error:
        raise InstallationError(
            f"cannot revalidate hook document: {error.strerror}"
        ) from error
    if latest.st_nlink != 1 or (latest.st_dev, latest.st_ino) != (
        current.device,
        current.inode,
    ):
        raise InstallationError("hook document changed before removal")
    os.unlink(plan.target.name, dir_fd=parent_fd)
    os.fsync(parent_fd)
    return True


def apply(plan: Plan) -> bool:
    """Apply a previously inspected plan after locked revalidation."""
    if plan.action in {Action.CONFLICT, Action.REFUSE}:
        raise InstallationError(plan.message)
    if plan.action is Action.NOOP:
        current = _inspect(plan.target, plan.expected, plan.operation)
        if current.action is Action.NOOP:
            return False
        raise InstallationError("configuration changed after planning")
    parent_fd = _open_locked_parent(plan)
    try:
        if plan.action is Action.CREATE:
            return _publish(plan, parent_fd)
        if plan.action is Action.REMOVE:
            return _remove(plan, parent_fd)
        raise InstallationError("unsupported installation action")
    finally:
        fcntl.flock(parent_fd, fcntl.LOCK_UN)
        os.close(parent_fd)
