from pathlib import Path

import pytest

from shim_guard.config import (
    DEFAULT_ENTITIES,
    config_path,
    load_entities,
    render_entities,
)


def test_entity_settings_default_preset_and_round_trip_a_selection(
    tmp_path: Path,
) -> None:
    target = tmp_path / "shim-guard" / "config.toml"

    assert load_entities(target) == DEFAULT_ENTITIES
    assert "FILE_PATH" not in DEFAULT_ENTITIES

    target.parent.mkdir()
    target.write_bytes(render_entities(("SECRET", "EMAIL")))

    assert load_entities(target) == ("EMAIL", "SECRET")

    target.write_bytes(render_entities(()))
    assert load_entities(target) == ()


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b'enabled_entities = ["UNKNOWN"]\n',
        b'enabled_entities = ["EMAIL", "EMAIL"]\n',
        b'enabled_entities = ["EMAIL"]\nextra = true\n',
    ],
)
def test_invalid_entity_settings_fail_safely(tmp_path: Path, content: bytes) -> None:
    target = tmp_path / "config.toml"
    target.write_bytes(content)

    with pytest.raises(ValueError, match="settings"):
        load_entities(target)


def test_unsafe_or_relative_settings_paths_are_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source.toml"
    source.write_bytes(render_entities(("EMAIL",)))
    link = tmp_path / "config.toml"
    link.symlink_to(source)

    with pytest.raises(ValueError, match="safely"):
        load_entities(link)

    monkeypatch.setenv("SHIM_GUARD_CONFIG", "relative/config.toml")
    with pytest.raises(ValueError, match="path"):
        config_path()
