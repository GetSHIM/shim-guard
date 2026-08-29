"""The summary is the product's only visible output when it succeeds."""

from __future__ import annotations

import pytest

from shim_guard.session import summary


def _record(**changes: object) -> dict:
    record = {
        "client": "claude",
        "event": "PostToolUse",
        "tool_name": "Read",
        "target": "/work/service/.env",
        "action": "mask",
        "entities": {"SECRET": 1},
        "latency_ms": 6,
    }
    record.update(changes)
    return record


def test_a_session_where_nothing_happened_says_nothing() -> None:
    """Silence is the correct output. A summary of zero findings is noise."""
    assert summary.render([]) == ""
    assert summary.render([_record(action="allow", entities={})]) == ""


def test_the_summary_groups_by_action_then_entity() -> None:
    text = summary.render(
        [
            _record(entities={"SECRET": 2}),
            _record(tool_name="Bash", target="", entities={"SECRET": 1}),
            _record(target="/work/docker-compose.yml", entities={"DB_URI": 2}),
            _record(
                event="UserPromptSubmit",
                tool_name="",
                target="",
                action="report",
                entities={"EMAIL": 1},
                latency_ms=14,
            ),
            _record(tool_name="Bash", target="", action="deny", entities={"SECRET": 1}),
            _record(action="allow", entities={}, latency_ms=4),
        ]
    )

    assert text.splitlines() == [
        "shim — this session",
        "  masked    3 SECRET  (Bash, Read .env)",
        "            2 DB_URI  (Read docker-compose.yml)",
        "  blocked   1 SECRET  (Bash)",
        "  warned    1 EMAIL  (your prompt)",
        "  overhead  6 ms median, 14 ms p95",
    ]


def test_only_the_file_name_is_shown_not_the_path() -> None:
    """A full path leaks directory layout for no benefit to the reader."""
    text = summary.render([_record(target="/home/someone/private/project/.env")])

    assert "(Read .env)" in text
    assert "/home/someone" not in text


def test_a_url_target_is_kept_whole() -> None:
    text = summary.render(
        [_record(tool_name="WebFetch", target="https://example.com/a/b")]
    )

    assert "(WebFetch https://example.com/a/b)" in text


def test_many_places_are_counted_rather_than_listed() -> None:
    records = [
        _record(target=f"/work/file{index}.env", entities={"SECRET": 1})
        for index in range(6)
    ]

    text = summary.render(records)

    assert "+3 more" in text
    assert text.count("Read file") == summary.MAX_SOURCES


def test_a_capped_session_says_the_count_is_short() -> None:
    text = summary.render([_record()], capped=True)

    assert "size cap" in text


@pytest.mark.parametrize("field", ["entities", "latency_ms", "action"])
def test_a_record_missing_a_field_does_not_break_the_summary(field: str) -> None:
    """The spool is read back from disk and may hold anything."""
    record = _record()
    del record[field]

    summary.render([record, _record()])


def test_json_carries_the_same_facts_as_the_text() -> None:
    records = [
        _record(entities={"SECRET": 2}),
        _record(action="report", entities={"EMAIL": 1}, latency_ms=14),
        _record(action="allow", entities={}),
    ]

    document = summary.as_json(records)

    assert document["events"] == 3
    assert document["acted"] == 2
    assert document["actions"]["mask"]["entities"] == {"SECRET": 2}
    assert document["actions"]["report"]["entities"] == {"EMAIL": 1}
    assert document["overhead_ms"] == {"median": 6, "p95": 14}
    assert document["capped"] is False


def test_a_record_with_unreadable_counts_produces_no_heading() -> None:
    """A heading with no lines under it claims something it will not name.

    The spool is read back from disk, so a record can arrive with `entities`
    in a shape the summary cannot total. That must read as silence, not as an
    empty announcement.
    """
    assert summary.render([_record(entities=["SECRET"])]) == ""
    assert summary.render([_record(entities=None)]) == ""
    assert summary.render([_record(entities={})]) == ""


def test_one_unreadable_record_does_not_hide_a_readable_one() -> None:
    text = summary.render(
        [_record(entities=["SECRET"]), _record(entities={"EMAIL": 1})]
    )

    assert "1 EMAIL" in text
    assert text.startswith("shim — this session")
