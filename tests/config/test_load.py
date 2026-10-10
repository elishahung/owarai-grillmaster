from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from grillmaster.config.errors import ConfigError, ConfigNotFoundError
from grillmaster.config.load import find_config, load_config

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def test_finds_config_in_an_ancestor_directory(
    tmp_path: Path, roles_toml: str, write_toml: Callable[[Path, str], Path]
):
    path = write_toml(tmp_path / "home", roles_toml)
    nested = tmp_path / "home" / "projects" / "abc"
    nested.mkdir(parents=True)

    assert find_config(nested, env={}) == path.resolve()


def test_nearest_config_wins_over_ancestors_and_grill_home(
    tmp_path: Path, roles_toml: str, write_toml: Callable[[Path, str], Path]
):
    write_toml(tmp_path, roles_toml)
    inner = write_toml(tmp_path / "inner", roles_toml)
    elsewhere = write_toml(tmp_path / "elsewhere", roles_toml)

    found = find_config(tmp_path / "inner", env={"GRILL_HOME": str(elsewhere.parent)})
    assert found == inner.resolve()


def test_falls_back_to_grill_home(
    tmp_path: Path, roles_toml: str, write_toml: Callable[[Path, str], Path]
):
    home = write_toml(tmp_path / "home", roles_toml)
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    assert find_config(cwd, env={"GRILL_HOME": str(home.parent)}) == home.resolve()


def test_missing_config_names_every_place_searched(tmp_path: Path):
    with pytest.raises(ConfigNotFoundError, match="GRILL_HOME is not set") as caught:
        find_config(tmp_path, env={})
    assert str(tmp_path.resolve()) in str(caught.value)
    assert "grill.example.toml" in str(caught.value)


def test_missing_config_in_grill_home_is_reported(tmp_path: Path):
    home = tmp_path / "home"
    with pytest.raises(ConfigNotFoundError, match="home"):
        find_config(tmp_path / "cwd", env={"GRILL_HOME": str(home)})


def test_load_returns_root_config_and_secrets(
    tmp_path: Path, roles_toml: str, write_toml: Callable[[Path, str], Path]
):
    root = tmp_path / "home"
    write_toml(root, f'[paths]\ncookies = "cookies.txt"\n[agents]\n{roles_toml}')
    (root / ".env").write_text("ELEVENLABS_API_KEY=k\n", encoding="utf-8")

    loaded = load_config(root, env={})

    assert loaded.root == root.resolve()
    assert loaded.path == root.resolve() / "grill.toml"
    assert loaded.config.paths.cookies == root.resolve() / "cookies.txt"
    assert loaded.secrets.elevenlabs_api_key is not None
    assert loaded.secrets.elevenlabs_api_key.get_secret_value() == "k"


def test_byte_order_mark_is_accepted(
    tmp_path: Path, roles_toml: str, write_toml: Callable[[Path, str], Path]
):
    write_toml(tmp_path, "﻿" + roles_toml)
    assert load_config(tmp_path, env={}).config.agents.max_concurrent == 5


def test_invalid_toml_fails_with_the_path(
    tmp_path: Path, write_toml: Callable[[Path, str], Path]
):
    path = write_toml(tmp_path, "[agents\n")
    with pytest.raises(ConfigError, match="not valid TOML") as caught:
        load_config(tmp_path, env={})
    assert str(path) in str(caught.value)


def test_invalid_settings_fail_with_the_path(
    tmp_path: Path, roles_toml: str, write_toml: Callable[[Path, str], Path]
):
    path = write_toml(tmp_path, f"{roles_toml}[translate]\nchunk_char_limit = 0\n")
    with pytest.raises(ConfigError, match="chunk_char_limit") as caught:
        load_config(tmp_path, env={})
    assert str(path) in str(caught.value)
