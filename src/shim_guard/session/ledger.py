"""Opt-in persistence: the same records, kept past the end of the session.

Off by default, and the default matters more than the feature. The session
spool is deleted when the client closes; this is the only thing in shim that
survives that, so it exists only because the user asked for it by name.

Files are one per month and retention deletes whole files. So the exact promise
is: **a month's records are deleted
``RETENTION_DAYS`` days after the end of that month**, which means an entry
written on the first of a month outlives one written on the last by up to the
length of the month. `docs/privacy.md` says this in the same words rather than
rounding it to "30 days".
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import stat
from pathlib import Path

#: Resolved 29 Aug 2026. Minimal data at rest is the defensible default.
RETENTION_DAYS = 30
MAX_LEDGER_BYTES = 5_000_000
_DIR_MODE = 0o700
_FILE_MODE = 0o600
_PREFIX = "ledger-"
_SUFFIX = ".jsonl"


class LedgerError(RuntimeError):
    """The ledger could not be used safely. Callers record nothing."""


def root_path() -> Path:
    """Return the directory the ledger lives in."""
    configured = os.environ.get("SHIM_GUARD_STATE_DIR")
    if configured:
        root = Path(configured).expanduser()
    elif xdg := os.environ.get("XDG_STATE_HOME"):
        root = Path(xdg).expanduser() / "shim-guard"
    else:
        try:
            root = Path.home() / ".local" / "state" / "shim-guard"
        except RuntimeError as error:
            raise LedgerError("ledger directory is invalid") from error
    if not root.is_absolute() or ".." in root.parts:
        raise LedgerError("ledger directory is invalid")
    return root


def _month(when: datetime.datetime) -> str:
    return f"{_PREFIX}{when.year:04d}-{when.month:02d}{_SUFFIX}"


def _open_root() -> int:
    path = root_path()
    try:
        path.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    except OSError as error:
        raise LedgerError("ledger directory could not be opened") from error
    try:
        info = os.fstat(descriptor)
        if info.st_uid != getattr(os, "getuid", lambda: info.st_uid)():
            raise LedgerError("ledger directory belongs to another user")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise LedgerError("ledger directory is readable by other users")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def files() -> list:
    """Return the ledger files, oldest month first."""
    try:
        return sorted(
            path for path in root_path().glob(f"{_PREFIX}*{_SUFFIX}") if path.is_file()
        )
    except OSError as error:
        raise LedgerError("ledger could not be listed") from error


def _month_end(path: Path) -> datetime.datetime | None:
    """Return the first instant after the month a file is named for."""
    stem = path.name[len(_PREFIX) : -len(_SUFFIX)]
    try:
        year, month = (int(part) for part in stem.split("-", 1))
        start = datetime.datetime(year, month, 1, tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None
    return (start + datetime.timedelta(days=31)).replace(day=1)


def prune(now: datetime.datetime | None = None) -> int:
    """Delete months past the retention window. Returns how many went.

    Age comes from the file's own name, not its modification time: the name is
    what says which records are inside, and an mtime is reset by a restore, a
    copy or a stray `touch`, any of which would silently extend retention.
    """
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    removed = 0
    for path in files():
        end = _month_end(path)
        if end is None or end + datetime.timedelta(days=RETENTION_DAYS) > moment:
            continue
        with contextlib.suppress(OSError):
            path.unlink()
            removed += 1
    return removed


def append(entry: dict, now: datetime.datetime | None = None) -> bool:
    """Append one record and prune expired months. False when at the cap."""
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True).encode() + b"\n"
    prune(moment)
    root = _open_root()
    try:
        descriptor = os.open(
            _month(moment),
            os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
            _FILE_MODE,
            dir_fd=root,
        )
        try:
            if os.fstat(descriptor).st_size + len(line) > MAX_LEDGER_BYTES:
                return False
            os.write(descriptor, line)
            return True
        finally:
            os.close(descriptor)
    except OSError as error:
        raise LedgerError("ledger could not be written") from error
    finally:
        os.close(root)


def entries(since: datetime.datetime | None = None) -> list:
    """Return every retained record, oldest first, optionally bounded."""
    found = []
    boundary = since.isoformat().replace("+00:00", "Z") if since else ""
    for path in files():
        try:
            content = path.read_bytes()
        except OSError as error:
            raise LedgerError("ledger could not be read") from error
        for line in content.decode("utf-8", "replace").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            if boundary and str(entry.get("ts", "")) < boundary:
                continue
            found.append(entry)
    return found


def purge() -> int:
    """Delete every ledger file. Returns how many went."""
    removed = 0
    for path in files():
        with contextlib.suppress(OSError):
            path.unlink()
            removed += 1
    return removed


__all__ = [
    "MAX_LEDGER_BYTES",
    "RETENTION_DAYS",
    "LedgerError",
    "append",
    "entries",
    "files",
    "prune",
    "purge",
    "root_path",
]
