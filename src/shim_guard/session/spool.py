from __future__ import annotations

import contextlib
import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path

MAX_SPOOL_BYTES = 1_000_000
MAX_ENTRY_BYTES = 2_048
_DIR_MODE = 0o700
_FILE_MODE = 0o600


class SpoolError(RuntimeError):
    pass


def _identity() -> int:
    return getattr(os, "getuid", lambda: 0)()


def root_path() -> Path:
    configured = os.environ.get("SHIM_GUARD_SESSION_DIR")
    if configured:
        root = Path(configured).expanduser()
        if not root.is_absolute() or ".." in root.parts:
            raise SpoolError("session directory is invalid")
        return root
    return Path(tempfile.gettempdir()) / f"shim-guard-session-{_identity()}"


@contextlib.contextmanager
def _root() -> Iterator[int]:
    path = root_path()
    try:
        path.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    except OSError as error:
        raise SpoolError("session directory could not be opened") from error
    try:
        info = os.fstat(descriptor)
        if info.st_uid != _identity():
            raise SpoolError("session directory belongs to another user")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise SpoolError("session directory is readable by other users")
        yield descriptor
    finally:
        os.close(descriptor)


def session_key(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8", "replace")).hexdigest()[:32]


def _name(session_id: str, suffix: str) -> str:
    return f"{session_key(session_id)}{suffix}"


def _read(root: int, name: str, limit: int) -> bytes:
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root)
    except FileNotFoundError:
        return b""
    except OSError as error:
        raise SpoolError("session spool could not be read") from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SpoolError("session spool is not a regular file")
        return os.read(descriptor, limit)
    except OSError as error:
        raise SpoolError("session spool could not be read") from error
    finally:
        os.close(descriptor)


def _parse(content: bytes) -> list:
    found = []
    for line in content.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            found.append(entry)
    return found


def append(session_id: str, entry: dict) -> bool:
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True).encode() + b"\n"
    if len(line) > MAX_ENTRY_BYTES:
        raise SpoolError("session record is too large")
    with _root() as root:
        try:
            descriptor = os.open(
                _name(session_id, ".jsonl"),
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                _FILE_MODE,
                dir_fd=root,
            )
        except OSError as error:
            raise SpoolError("session spool could not be opened") from error
        try:
            if os.fstat(descriptor).st_size + len(line) > MAX_SPOOL_BYTES:
                return False
            os.write(descriptor, line)
            return True
        except OSError as error:
            raise SpoolError("session spool could not be written") from error
        finally:
            os.close(descriptor)


def _at_cap(root: int, name: str) -> bool:
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root)
    except OSError:
        return False
    try:
        return os.fstat(descriptor).st_size + MAX_ENTRY_BYTES > MAX_SPOOL_BYTES
    finally:
        os.close(descriptor)


def capped(session_id: str) -> bool:
    return capped_for_stem(session_key(session_id))


def capped_for_stem(stem: str) -> bool:
    if not stem.isalnum():
        raise SpoolError("session spool name is invalid")
    with _root() as root:
        return _at_cap(root, f"{stem}.jsonl")


def entries(session_id: str) -> list:
    return entries_for_stem(session_key(session_id))


def entries_for_stem(stem: str) -> list:
    if not stem.isalnum():
        raise SpoolError("session spool name is invalid")
    with _root() as root:
        return _parse(_read(root, f"{stem}.jsonl", MAX_SPOOL_BYTES))


def summarized(session_id: str) -> int:
    with _root() as root:
        content = _read(root, _name(session_id, ".mark"), 32)
    try:
        return max(0, int(content.decode("ascii", "replace").strip() or 0))
    except ValueError:
        return 0


def mark_summarized(session_id: str, count: int) -> None:
    with _root() as root:
        try:
            descriptor = os.open(
                _name(session_id, ".mark"),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                _FILE_MODE,
                dir_fd=root,
            )
            try:
                os.write(descriptor, str(max(0, int(count))).encode("ascii"))
            finally:
                os.close(descriptor)
        except OSError as error:
            raise SpoolError("session mark could not be written") from error


def clear(session_id: str) -> None:
    with _root() as root:
        try:
            for suffix in (".jsonl", ".mark"):
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(_name(session_id, suffix), dir_fd=root)
        except OSError as error:
            raise SpoolError("session spool could not be removed") from error


def newest() -> str:
    found = []
    try:
        for path in root_path().glob("*.jsonl"):
            with contextlib.suppress(OSError):
                info = path.stat()
                if stat.S_ISREG(info.st_mode):
                    found.append((info.st_mtime, path.stem))
    except OSError as error:
        raise SpoolError("session directory could not be listed") from error
    if not found:
        return ""
    return max(found)[1]


__all__ = [
    "MAX_SPOOL_BYTES",
    "SpoolError",
    "append",
    "capped",
    "capped_for_stem",
    "clear",
    "entries",
    "entries_for_stem",
    "mark_summarized",
    "newest",
    "root_path",
    "session_key",
    "summarized",
]
