"""`shim report` — what shim did, on demand rather than at the end of a turn.

The hook emits the same summary at `Stop`, but only once per change and only
inside the client. This is the way to ask for it: from a script, when the
message scrolled away, or after the client has closed.

That last case only works if the ledger is on. The spool is deleted at
`SessionEnd` — that is the point of it — so once the client exits, the retained
ledger is the only thing left to read, and this reads it.
"""

from __future__ import annotations

import sys

import typer

from shim_guard.cli.output import emit, emit_json, terminal_text
from shim_guard.session import spool, summary


def _retained() -> list:
    """Return the newest retained session's records, or ``[]``.

    Records from many sessions share one monthly file, so the newest timestamp
    selects the session and the session id then selects its records — "the most
    recent session", not "the last few things that happened".
    """
    from shim_guard.session import ledger

    try:
        entries = ledger.entries()
    except (ledger.LedgerError, OSError):
        return []
    if not entries:
        return []
    newest = max(entries, key=lambda entry: str(entry.get("ts", "")))
    session = newest.get("session_id")
    return [entry for entry in entries if entry.get("session_id") == session]


def report(*, as_json: bool) -> None:
    """Print the most recent session's summary, live or retained."""
    try:
        stem = spool.newest()
        records = spool.entries_for_stem(stem) if stem else []
        truncated = spool.capped_for_stem(stem) if stem else False
    except (spool.SpoolError, OSError):
        if as_json:
            emit_json("report", "error", error="session records could not be read")
        else:
            emit("FAIL", "Session records could not be read.", error=True)
        raise typer.Exit(2) from None

    source = "session"
    if not records:
        records = _retained()
        source = "ledger" if records else "none"

    if as_json:
        document = summary.as_json(records, truncated)
        emit_json("report", "ok", source=source, **document)
        return

    if not records:
        emit(
            "WARN",
            "No session on record. shim writes one while a client session is open "
            "and deletes it when the session ends; `shim config --ledger` keeps it.",
        )
        raise typer.Exit(1)

    text = summary.render(records, truncated)
    if not text:
        emit("PASS", f"shim inspected {len(records)} events and found nothing.")
        return
    # The summary is multi-line, so newlines have to survive the escaping
    # that keeps a file name from repainting the terminal.
    print(terminal_text(text, sys.stdout, "\n"))
    if source == "ledger":
        emit("WARN", "From the retained ledger; the live session has ended.")


def purge(*, yes: bool, as_json: bool) -> None:
    """Delete every retained record. The spool is separate and untouched."""
    from shim_guard.session import ledger

    try:
        existing = ledger.files()
    except (ledger.LedgerError, OSError):
        if as_json:
            emit_json("ledger-purge", "error", error="ledger could not be read")
        else:
            emit("FAIL", "The ledger could not be read.", error=True)
        raise typer.Exit(2) from None

    if not existing:
        if as_json:
            emit_json("ledger-purge", "ok", removed=0)
        else:
            emit("PASS", "There is nothing retained to delete.")
        return

    if not as_json and not yes:
        emit("WARN", f"{len(existing)} retained file(s) at {ledger.root_path()}.")
        if not typer.confirm("Delete them?", default=False):
            emit("WARN", "Nothing was deleted.")
            raise typer.Exit(1)
    if as_json and not yes:
        emit_json("ledger-purge", "error", error="--yes is required with --json")
        raise typer.Exit(2)

    removed = ledger.purge()
    if as_json:
        emit_json("ledger-purge", "ok", removed=removed)
    else:
        emit("PASS", f"Deleted {removed} retained file(s).")
