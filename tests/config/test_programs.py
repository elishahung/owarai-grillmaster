from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from grillmaster.config.errors import ConfigError
from grillmaster.config.load import read_config
from grillmaster.config.model import validate_config
from grillmaster.config.programs import (
    ProgramRules,
    register_program,
    resolve_program_rules,
)
from grillmaster.core.fs import atomic_write_text, exclusive_lock
from grillmaster.core.stage_key import StageKey

if TYPE_CHECKING:
    from collections.abc import Callable

    from grillmaster.config.model import AppConfig


@pytest.fixture
def config(minimal_data: dict[str, Any]) -> AppConfig:
    return validate_config(
        {
            **minimal_data,
            "package": {
                "inserts": [
                    {"pool": "judge", "output": "judge", "when": "remix"},
                    {"pool": "logo", "output": "logo", "when": "always"},
                    {"pool": "bell", "output": "bell", "when": "always"},
                    {"pool": "outro", "output": "outro", "when": "remix"},
                ]
            },
            "programs": {
                "series": {
                    "水曜日のダウンタウン": {
                        "inserts": ["judge"],
                        "instruction": {
                            "common": "  series common  ",
                            "prepass": "series prepass",
                            "chunks": "series chunks",
                            "refine": "   ",
                        },
                    },
                    "plain": {},
                },
                "channel": {
                    "日テレ": {
                        "remix": True,
                        "inserts": ["bell", "judge"],
                        "instruction": {
                            "common": "channel common",
                            "chunks": "channel chunks",
                        },
                    },
                },
            },
        },
        root=Path(),
    )


def _resolve(
    config: AppConfig, *, series: str | None = None, channel: str | None = None
) -> ProgramRules:
    return resolve_program_rules(
        config.programs, config.package, series=series, channel=channel
    )


def test_channel_then_series_instructions(config: AppConfig):
    rules = _resolve(config, series="水曜日のダウンタウン", channel="日テレ")
    assert rules.instruction_text(StageKey.CHUNKS) == (
        "channel common\n\nchannel chunks\n\nseries common\n\nseries chunks"
    )


def test_common_applies_to_stages_without_their_own_text(config: AppConfig):
    rules = _resolve(config, series="水曜日のダウンタウン")
    assert rules.instruction_text(StageKey.GLOSSARY) == "series common"
    # Blank stage text is skipped.
    assert rules.instruction_text(StageKey.REFINE) == "series common"


def test_no_configured_text_is_empty(config: AppConfig):
    assert _resolve(config, series="plain").instruction_text(StageKey.PREPASS) == ""
    assert ProgramRules().instruction_text(StageKey.CHUNKS) == ""


def test_instructions_reject_stages_that_take_none(config: AppConfig):
    rules = _resolve(config, series="plain", channel="日テレ")
    with pytest.raises(ValueError, match="takes no program instructions"):
        rules.instruction_text(StageKey.CHAT_TRANSLATE)


def test_remix_is_on_when_any_entry_sets_it(config: AppConfig):
    assert _resolve(config, series="plain", channel="日テレ").remix
    assert not _resolve(config, series="plain").remix


def test_unknown_or_missing_names_match_no_entry(config: AppConfig):
    for rules in (_resolve(config), _resolve(config, series="x", channel="x")):
        assert not rules.remix
        assert rules.instructions == ()


@pytest.mark.parametrize(
    ("series", "channel", "expected"),
    [
        # Declaration order; `judge` and `bell` are listed by some program,
        # so only programs listing them get them.
        ("水曜日のダウンタウン", "日テレ", ["judge", "logo", "bell", "outro"]),
        ("水曜日のダウンタウン", None, ["judge", "logo", "outro"]),
        ("plain", None, ["logo", "outro"]),
        (None, None, ["logo", "outro"]),
    ],
)
def test_inserts_are_unscoped_plus_listed(
    config: AppConfig, series: str | None, channel: str | None, expected: list[str]
):
    rules = _resolve(config, series=series, channel=channel)
    assert [insert.output for insert in rules.inserts] == expected


@pytest.mark.parametrize(
    ("remix", "expected"), [(True, ["judge", "logo", "outro"]), (False, ["logo"])]
)
def test_inserts_for_filters_on_when(
    config: AppConfig, remix: bool, expected: list[str]
):
    rules = _resolve(config, series="水曜日のダウンタウン")
    assert [insert.output for insert in rules.inserts_for(remix=remix)] == expected


# -- registration -----------------------------------------------------------


def test_registers_new_names_at_the_end_keeping_the_file(
    tmp_path: Path, roles_toml: str, write_toml: Callable[[Path, str], Path]
):
    original = (
        "#:schema ./grill.schema.json\n"
        "# hand-written comment\n"
        f"[agents]  # inline comment\n{roles_toml}\n"
        '[programs.series."有吉の壁"]\n'
        "remix = true   # keep\n"
    )
    path = write_toml(tmp_path, original)

    added = register_program(path, series="ドキュメンタル", channel="Prime Video")

    assert added == ["series 'ドキュメンタル'", "channel 'Prime Video'"]
    assert path.read_text(encoding="utf-8") == (
        original
        + '\n[programs.series."ドキュメンタル"]\n\n[programs.channel."Prime Video"]\n'
    )
    config = read_config(path)
    assert set(config.programs.series) == {"有吉の壁", "ドキュメンタル"}
    assert config.programs.series["有吉の壁"].remix
    assert set(config.programs.channel) == {"Prime Video"}


def test_known_names_leave_the_file_untouched(
    tmp_path: Path, roles_toml: str, write_toml: Callable[[Path, str], Path]
):
    original = f'{roles_toml}[programs.series."S"]\n[programs.channel."C"]\n'
    path = write_toml(tmp_path, original)
    before = path.stat().st_mtime_ns

    assert register_program(path, series="S", channel="C") == []
    assert register_program(path, series=None, channel=None) == []
    assert register_program(path, series="", channel="") == []
    assert path.read_text(encoding="utf-8") == original
    assert path.stat().st_mtime_ns == before


def test_registers_only_the_missing_section(
    tmp_path: Path, roles_toml: str, write_toml: Callable[[Path, str], Path]
):
    path = write_toml(tmp_path, f'{roles_toml}[programs.series."S"]\n')
    assert register_program(path, series="S", channel="C") == ["channel 'C'"]
    assert path.read_text(encoding="utf-8").endswith("\n\n[programs.channel.C]\n")


def test_names_needing_escapes_round_trip(
    tmp_path: Path, roles_toml: str, write_toml: Callable[[Path, str], Path]
):
    path = write_toml(tmp_path, roles_toml)
    name = 'say "hi". \\ back'
    register_program(path, series=name, channel=None)
    assert set(read_config(path).programs.series) == {name}


def test_missing_trailing_newline_and_crlf_and_bom_are_kept(
    tmp_path: Path, roles_toml: str, write_toml: Callable[[Path, str], Path]
):
    original = "﻿" + roles_toml.replace("\n", "\r\n").removesuffix("\r\n")
    path = write_toml(tmp_path, original)

    register_program(path, series="S", channel=None)

    assert path.read_bytes().decode("utf-8") == (
        original + "\r\n\r\n[programs.series.S]\r\n"
    )
    assert set(read_config(path).programs.series) == {"S"}


def test_programs_written_inline_cannot_be_appended_to(
    tmp_path: Path, roles_toml: str, write_toml: Callable[[Path, str], Path]
):
    original = f"programs = {{ series = {{}} }}\n{roles_toml}"
    path = write_toml(tmp_path, original)
    with pytest.raises(ConfigError, match="would break the file"):
        register_program(path, series="S", channel=None)
    assert path.read_text(encoding="utf-8") == original


def test_concurrent_registrations_keep_every_entry(
    tmp_path: Path, roles_toml: str, write_toml: Callable[[Path, str], Path]
):
    # Two downloads register while a third process holds the file's lock and
    # appends its own entry: each writer must re-read under the lock, or the
    # last write drops the others' entries.
    path = write_toml(tmp_path, roles_toml)
    lock = path.with_name(f"{path.name}.lock")
    writers = [
        threading.Thread(
            target=register_program,
            args=(path,),
            kwargs={"series": name, "channel": None},
        )
        for name in ("A", "B")
    ]
    with exclusive_lock(lock, timeout=0):
        for writer in writers:
            writer.start()
        time.sleep(0.2)
        assert path.read_text(encoding="utf-8") == roles_toml  # both wait
        atomic_write_text(path, roles_toml + '\n[programs.channel."C"]\n')
    for writer in writers:
        writer.join(10)

    config = read_config(path)
    assert set(config.programs.series) == {"A", "B"}
    assert set(config.programs.channel) == {"C"}


def test_a_stuck_lock_holder_fails_the_registration(
    tmp_path: Path, roles_toml: str, write_toml: Callable[[Path, str], Path]
):
    path = write_toml(tmp_path, roles_toml)
    with (
        exclusive_lock(path.with_name(f"{path.name}.lock"), timeout=0),
        pytest.raises(ConfigError, match=r"grill\.toml\.lock"),
    ):
        register_program(path, series="S", channel=None, lock_timeout=0.1)
    assert path.read_text(encoding="utf-8") == roles_toml
