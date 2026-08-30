"""The short-lived, per-session record of what shim did.

Hooks are separate processes: nothing survives between two events except what
is written down. "In memory" is the user-facing promise and this is how it is
kept — a file under the OS temporary directory, owned by the user, readable by
nobody else, deleted when the session ends.

There is exactly one rule about its contents, inherited from ``events.record``:
no entry ever carries payload text. Entity names and counts, yes; the value
that produced them, never. ``tests/session`` asserts it by scanning a recorded
session for the secrets it injected.

The session identifier is hashed rather than used as a file name. It comes from
the client and a name is a path — hashing removes traversal as a question
rather than answering it, and keeps the identifier itself off the filesystem.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path

#: A spool stops growing here. Entries are ~200 bytes, so this is a very long
#: session; past it the summary undercounts and says so rather than filling the
#: user's temporary directory.
MAX_SPOOL_BYTES = 1_000_000
#: Several hook processes append to one spool at once — Claude Code runs
#: tools in parallel. Concurrent `O_APPEND` writes do not interleave at
#: these sizes; the worst record this code can produce is ~811 bytes, so
#: the cap leaves headroom without approaching the size where that stops
#: being reliable. `tests/session` proves both halves.
MAX_ENTRY_BYTES = 2_048
_DIR_MODE = 0o700
_FILE_MODE = 0o600


class SpoolError(RuntimeError):
    """The spool could not be used safely. Callers allow the event anyway."""


def _identity() -> int:
    return getattr(os, "getuid", lambda: 0)()


def root_path() -> Path:
    """Return the directory the spools live in."""
    configured = os.environ.get("SHIM_GUARD_SESSION_DIR")
    if configured:
        root = Path(configured).expanduser()
        if not root.is_absolute() or ".." in root.parts:
            raise SpoolError("session directory is invalid")
        return root
    return Path(tempfile.gettempdir()) / f"shim-guard-session-{_identity()}"


@contextlib.contextmanager
def _root() -> Iterator[int]:
    """Yield a descriptor for the spool directory, creating it if needed.

    The directory lives in a world-writable place, so it is opened without
    following links and checked for ownership and mode before anything is
    written into it. Every path below is then resolved relative to this
    descriptor, so the directory cannot be swapped underneath us.
    """
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


def _name(session_id: str, suffix: str) -> str:
    digest = hashlib.sha256(session_id.encode("utf-8", "replace")).hexdigest()
    return f"{digest[:32]}{suffix}"


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
    """Append one record. Returns False when the spool is at its cap."""
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
        # `append` refuses when the *next* record would cross the cap, so a
        # full spool sits just below it and never reaches it. The reader does
        # not know how big the refused record was, so it uses the largest one
        # that could exist. The error is at most one record's worth of
        # headroom, and it errs towards warning — which is the safe direction,
        # since the alternative is a total the user believes is complete.
        return os.fstat(descriptor).st_size + MAX_ENTRY_BYTES > MAX_SPOOL_BYTES
    finally:
        os.close(descriptor)


def capped(session_id: str) -> bool:
    """Return whether this spool stopped accepting records.

    Only `append` saw the cap, and only the reader can tell the user about it,
    so the size is asked for again here rather than carried between processes.
    An `fstat` costs nothing next to re-reading a megabyte.
    """
    with _root() as root:
        return _at_cap(root, _name(session_id, ".jsonl"))


def capped_for_stem(stem: str) -> bool:
    """Return whether an already-hashed spool is at its cap."""
    if not stem.isalnum():
        raise SpoolError("session spool name is invalid")
    with _root() as root:
        return _at_cap(root, f"{stem}.jsonl")


def entries(session_id: str) -> list:
    """Return this session's records, skipping any line that is not one."""
    with _root() as root:
        return _parse(_read(root, _name(session_id, ".jsonl"), MAX_SPOOL_BYTES))


def entries_for_stem(stem: str) -> list:
    """Return the records of an already-hashed spool, for `shim report`."""
    if not stem.isalnum():
        raise SpoolError("session spool name is invalid")
    with _root() as root:
        return _parse(_read(root, f"{stem}.jsonl", MAX_SPOOL_BYTES))


def summarized(session_id: str) -> int:
    """Return how many records the user has already been shown."""
    with _root() as root:
        content = _read(root, _name(session_id, ".mark"), 32)
    try:
        return max(0, int(content.decode("ascii", "replace").strip() or 0))
    except ValueError:
        return 0


def mark_summarized(session_id: str, count: int) -> None:
    """Remember how many records have been shown, so the next turn is quiet."""
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
    """Delete this session's spool. Called at `SessionEnd`."""
    with _root() as root:
        for suffix in (".jsonl", ".mark"):
            try:
                os.unlink(_name(session_id, suffix), dir_fd=root)
            except FileNotFoundError:
                continue
            except OSError as error:
                raise SpoolError("session spool could not be removed") from error


def newest() -> str:
    """Return the file stem of the most recently written spool, or ``""``.

    `shim report` runs in a different process from the hooks and is not told a
    session identifier, so it reports on whichever session was last active.
    """
    # Another client's `SessionEnd` can unlink its spool between the listing
    # and the stat. That is somebody else's session ending normally, so it is
    # skipped rather than reported as "your records are unreadable".
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
    "summarized",
]
