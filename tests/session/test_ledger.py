"""The one thing shim keeps past the end of a session, and only on request."""

from __future__ import annotations

import datetime
import json
import stat
from pathlib import Path

import pytest

from shim_guard.session import ledger

JANUARY = datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc)
FEBRUARY = datetime.datetime(2026, 2, 15, tzinfo=datetime.timezone.utc)


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHIM_GUARD_STATE_DIR", str(tmp_path / "state"))


def _entry(**changes: object) -> dict:
    entry = {
        "action": "mask",
        "entities": {"SECRET": 1},
        "tool_name": "Read",
        "ts": "2026-01-15T00:00:00Z",
    }
    entry.update(changes)
    return entry


def test_records_are_filed_by_month() -> None:
    ledger.append(_entry(), JANUARY)
    ledger.append(_entry(), FEBRUARY)

    assert [path.name for path in ledger.files()] == [
        "ledger-2026-01.jsonl",
        "ledger-2026-02.jsonl",
    ]


def test_the_ledger_is_private_to_its_owner() -> None:
    ledger.append(_entry(), JANUARY)

    assert stat.S_IMODE(ledger.root_path().stat().st_mode) == 0o700
    for path in ledger.files():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


#: January's records expire ``RETENTION_DAYS`` after 1 February, not after the
#: day each one was written. This is the exact promise `docs/privacy.md` makes.
JANUARY_EXPIRES = datetime.datetime(
    2026, 2, 1, tzinfo=datetime.timezone.utc
) + datetime.timedelta(days=ledger.RETENTION_DAYS)


def test_retention_removes_a_month_that_has_aged_out() -> None:
    ledger.append(_entry(), JANUARY)
    assert len(ledger.files()) == 1

    assert ledger.prune(JANUARY_EXPIRES + datetime.timedelta(seconds=1)) == 1
    assert ledger.files() == []


def test_retention_keeps_a_month_until_the_window_closes() -> None:
    ledger.append(_entry(), JANUARY)

    assert ledger.prune(JANUARY + datetime.timedelta(days=1)) == 0
    assert ledger.prune(JANUARY_EXPIRES - datetime.timedelta(seconds=1)) == 0
    assert len(ledger.files()) == 1


def test_retention_does_not_depend_on_the_modification_time() -> None:
    """A restore, a copy or a stray touch must not extend retention."""
    import os

    ledger.append(_entry(), JANUARY)
    aged = ledger.files()[0]
    os.utime(aged, None)  # as if it had just been written

    assert ledger.prune(JANUARY_EXPIRES + datetime.timedelta(seconds=1)) == 1


def test_a_file_whose_name_is_not_a_month_is_left_alone() -> None:
    ledger.append(_entry(), JANUARY)
    stray = ledger.root_path() / "ledger-not-a-month.jsonl"
    stray.write_text("", encoding="utf-8")

    ledger.prune(JANUARY_EXPIRES + datetime.timedelta(days=400))

    assert stray.exists()


def test_appending_prunes_without_being_asked() -> None:
    """Nothing else runs on a schedule, so the write path has to do it."""
    ledger.append(_entry(), JANUARY)

    later = JANUARY_EXPIRES + datetime.timedelta(days=1)
    ledger.append(_entry(ts="2026-03-05T00:00:00Z"), later)

    assert [path.name for path in ledger.files()] == ["ledger-2026-03.jsonl"]


def test_entries_read_back_across_months_in_order() -> None:
    ledger.append(_entry(tool_name="Read"), JANUARY)
    ledger.append(_entry(tool_name="Bash", ts="2026-02-01T00:00:00Z"), FEBRUARY)

    found = ledger.entries()
    assert [entry["tool_name"] for entry in found] == ["Read", "Bash"]


def test_entries_can_be_bounded_by_time() -> None:
    ledger.append(_entry(tool_name="Read"), JANUARY)
    ledger.append(_entry(tool_name="Bash", ts="2026-02-01T00:00:00Z"), FEBRUARY)

    found = ledger.entries(since=FEBRUARY.replace(day=1))
    assert [entry["tool_name"] for entry in found] == ["Bash"]


def test_a_full_month_reports_rather_than_growing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ledger, "MAX_LEDGER_BYTES", 200)
    assert ledger.append(_entry(), JANUARY) is True

    for _ in range(10):
        if ledger.append(_entry(), JANUARY) is False:
            break
    else:  # pragma: no cover - only reached if the cap is not enforced
        raise AssertionError("the cap was never reached")

    assert ledger.files()[0].stat().st_size <= 200


def test_purge_deletes_everything() -> None:
    ledger.append(_entry(), JANUARY)
    ledger.append(_entry(), FEBRUARY)

    assert ledger.purge() == 2
    assert ledger.entries() == []
    assert ledger.purge() == 0


def test_an_absent_ledger_reads_as_empty() -> None:
    assert ledger.files() == []
    assert ledger.entries() == []


def test_a_directory_other_users_can_read_is_refused(tmp_path: Path) -> None:
    (tmp_path / "state").mkdir(mode=0o755, parents=True)

    with pytest.raises(ledger.LedgerError):
        ledger.append(_entry(), JANUARY)


def test_a_symlinked_month_is_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir(mode=0o700, parents=True)
    target = tmp_path / "stolen.jsonl"
    target.write_text("", encoding="utf-8")
    (root / "ledger-2026-01.jsonl").symlink_to(target)

    with pytest.raises(ledger.LedgerError):
        ledger.append(_entry(), JANUARY)
    assert target.read_text() == ""


def test_the_hook_writes_nothing_here_unless_the_policy_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off by default is the whole promise; this is where it is kept."""
    from shim_guard import hook
    from shim_guard.events.record import Record

    monkeypatch.setenv("SHIM_GUARD_SESSION_DIR", str(tmp_path / "spools"))
    record = Record(
        client="claude",
        event="PostToolUse",
        tool_name="Read",
        direction="inbound",
        mode="enforce",
        action="mask",
        entities=(("SECRET", 1),),
    )

    hook._remember("session", record, 5, ledger=False)
    assert ledger.files() == []

    hook._remember("session", record, 5, ledger=True)
    assert len(ledger.files()) == 1
    written = json.loads(ledger.files()[0].read_text().splitlines()[0])
    assert written["entities"] == {"SECRET": 1}
    assert written["session_id"] == "session"
