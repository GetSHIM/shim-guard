"""`shim report` — what shim did, on demand rather than at the end of a turn.

The hook emits the same summary at `Stop`, but only once per change and only
inside the client. This is the way to ask for it: after the client has closed,
from a script, or when the message scrolled away.
"""

from __future__ import annotations

import sys

import typer

from shim_guard.cli.output import emit, emit_json, terminal_text
from shim_guard.session import spool, summary


def report(*, as_json: bool) -> None:
    """Print the most recent session's summary."""
    try:
        stem = spool.newest()
        records = spool.entries_for_stem(stem) if stem else []
    except (spool.SpoolError, OSError):
        if as_json:
            emit_json("report", "error", error="session records could not be read")
        else:
            emit("FAIL", "Session records could not be read.", error=True)
        raise typer.Exit(2) from None

    if as_json:
        document = summary.as_json(records)
        emit_json("report", "ok", **document)
        return

    if not records:
        emit(
            "WARN",
            "No session on record. shim writes one while a client session is open, "
            "and deletes it when the session ends.",
        )
        raise typer.Exit(1)

    text = summary.render(records)
    if not text:
        emit("PASS", f"shim inspected {len(records)} events and found nothing.")
        return
    print(terminal_text(text, sys.stdout))


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
