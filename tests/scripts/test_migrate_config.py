from __future__ import annotations

import json
import shutil
import tomllib
from typing import TYPE_CHECKING

import pytest

from grillmaster.config.model import validate_config
from grillmaster.core.model_spec import ModelSpec, Role
from grillmaster.core.stage_key import StageKey
from grillmaster.package.pools import MediaPool

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from types import ModuleType

API_KEY_VALUE = "sk-test-not-a-real-key"


@pytest.fixture
def legacy_root(tmp_path: Path) -> Path:
    package = tmp_path / "package"
    (package / "placeholder").mkdir(parents=True)
    (package / "noise" / "default").mkdir(parents=True)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                f"ELEVENLABS_API_KEY={API_KEY_VALUE}",
                r"ARCHIVED_PATH=V:\show\grilled",
                f"PACKAGE_PATH={package}",
                "COOKIES_TXT_PATH=cookies.txt",
                "ENABLE_COVER_GENERATION=true",
                "AGENT_CONCURRENCY=3",
                "AGENT_PREPASS_MODEL=agy/gemini-3.1-pro/high",
                "AGENT_COMMON_MODEL=codex/gpt-6-astra/high",
                "AGENT_POSTPROCESS_MODEL=codex/gpt-6-astra/ultra",
                "# AGENT_CHUNK_MODEL=claude/opus",
                "SOMETHING_ELSE=1",
            ]
        ),
        encoding="utf-8",
    )
    config = {
        "$schema": "./config.schema.json",
        "series": {
            "水曜日のダウンタウン": {"remix": True},
            "POKER SONIC": {
                "instruction": {
                    "common": "撲克術語",
                    "translate": "a\nb",
                    "refine": None,
                }
            },
            "新番組": {},
        },
        "channel": {"日テレ": {"remix": True}},
    }
    (tmp_path / "config.json").write_text(
        json.dumps(config, ensure_ascii=False), encoding="utf-8"
    )
    return tmp_path


def test_build_renders_a_valid_grill_toml(
    migrate_config: ModuleType, legacy_root: Path
):
    text, _ = migrate_config.build(legacy_root)

    assert text.startswith("#:schema ./grill.schema.json\n")
    config = validate_config(tomllib.loads(text), root=legacy_root)
    assert str(config.paths.archive) == r"V:\show\grilled"
    assert config.paths.cookies == legacy_root / "cookies.txt"
    assert config.agents.max_concurrent == 3
    assert config.features.cover is True
    roles = config.agents.roles
    assert roles.prepass == ModelSpec.parse("agy/gemini-3.1-pro/high")
    # Unset in .env: the legacy default applies.
    assert roles.chunk == ModelSpec.parse("agy/gemini-3.1-pro")
    assert roles.spec(Role.CHAT) == roles.utility
    # Cover generation was Codex at the utility effort.
    assert roles.image == ModelSpec.parse("codex/gpt-6-astra/high")
    assert [insert.pool for insert in config.package.inserts] == ["placeholder"]


def test_program_rules_map_to_new_stage_keys(
    migrate_config: ModuleType, legacy_root: Path
):
    text, _ = migrate_config.build(legacy_root)

    programs = validate_config(tomllib.loads(text), root=legacy_root).programs
    assert programs.series["水曜日のダウンタウン"].remix is True
    poker = programs.series["POKER SONIC"].instruction
    assert poker.common == "撲克術語"
    assert poker.stage_text(StageKey.CHUNKS) == "a\nb"
    assert poker.refine is None
    assert "新番組" in programs.series
    assert programs.channel["日テレ"].remix is True


def test_notes_list_unused_keys_without_secrets(
    migrate_config: ModuleType, legacy_root: Path
):
    text, notes = migrate_config.build(legacy_root)

    joined = "\n".join(notes)
    assert ".env ELEVENLABS_API_KEY: stays in .env" in joined
    assert ".env AGENT_CONCURRENCY: now in grill.toml" in joined
    assert ".env SOMETHING_ELSE: unknown legacy key" in joined
    assert "pools" in joined
    assert API_KEY_VALUE not in joined
    assert API_KEY_VALUE not in text


def test_dry_run_prints_and_apply_writes_once(
    migrate_config: ModuleType,
    legacy_root: Path,
    capsys: pytest.CaptureFixture[str],
):
    target = legacy_root / "grill.toml"

    assert migrate_config.main(["--root", str(legacy_root)]) == 0
    assert not target.exists()
    assert "[agents.roles]" in capsys.readouterr().out

    assert migrate_config.main(["--root", str(legacy_root), "--apply"]) == 0
    written = target.read_text(encoding="utf-8")
    validate_config(tomllib.loads(written), root=legacy_root)

    with pytest.raises(SystemExit):
        migrate_config.main(["--root", str(legacy_root), "--apply"])
    assert target.read_text(encoding="utf-8") == written


TRICKY_INSTRUCTIONS = [
    "line one\nline two\n",
    "\nleading newline",
    "windows\r\nline endings",
    'quotes """ and \\ backslash\n\ttab',
    "trailing backslash \\\nnext",
]


@pytest.mark.parametrize("text", TRICKY_INSTRUCTIONS)
def test_multiline_instructions_round_trip_exactly(
    migrate_config: ModuleType, legacy_root: Path, text: str
):
    config = {"series": {"番組": {"instruction": {"translate": text}}}}
    (legacy_root / "config.json").write_text(json.dumps(config), encoding="utf-8")

    rendered, _ = migrate_config.build(legacy_root)

    parsed = tomllib.loads(rendered)
    assert parsed["programs"]["series"]["番組"]["instruction"]["chunks"] == text


def test_a_lossy_rendering_is_refused(
    migrate_config: ModuleType, legacy_root: Path, monkeypatch: pytest.MonkeyPatch
):
    def lossy(value: object) -> object:
        return value.replace("\n", " ") if isinstance(value, str) else value

    monkeypatch.setattr(migrate_config, "_toml_value", lossy)

    with pytest.raises(ValueError, match="round-trip the program rules"):
        migrate_config.build(legacy_root)


# --- pools -------------------------------------------------------------------


@pytest.fixture
def legacy_package(tmp_path: Path) -> Path:
    """`noise/default` (000..002, cursor at file 2 + 30 s), `noise/sleep`
    (no cursor) and `placeholder` (001..003, next clip 3)."""
    package = tmp_path / "package"
    default = package / "noise" / "default"
    default.mkdir(parents=True)
    for index in range(3):
        (default / f"{index:03d}.mp4").write_text(f"default {index}", "utf-8")
    (default / "state.json").write_text(
        json.dumps({"next_index": 2, "next_seconds": 30}), encoding="utf-8"
    )
    sleep = package / "noise" / "sleep"
    sleep.mkdir()
    (sleep / "000.mkv").write_text("sleep 0", "utf-8")
    placeholder = package / "placeholder"
    placeholder.mkdir()
    for index in range(1, 4):
        (placeholder / f"{index:03d}.mp4").write_text(f"judge {index}", "utf-8")
    (placeholder / "state.json").write_text('{"next_index": 3}', encoding="utf-8")
    return package


def tree(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


def test_pool_dry_run_prints_the_plan_and_moves_nothing(
    migrate_config: ModuleType,
    legacy_package: Path,
    capsys: pytest.CaptureFixture[str],
):
    before = tree(legacy_package)

    assert migrate_config.main(["--pools", str(legacy_package)]) == 0

    out = capsys.readouterr().out
    assert f"-> {legacy_package / 'pools' / 'default'}" in out
    assert "renumber 3 file(s): 000.mp4..002.mp4 -> 001.mp4..003.mp4" in out
    assert "state.json -> .cursor.json (index 2, seconds 30)" in out
    assert "state.json -> .cursor.json (index 2, seconds 0)" in out
    assert tree(legacy_package) == before


def test_pool_apply_moves_renumbers_and_converts_the_cursors(
    migrate_config: ModuleType, legacy_package: Path
):
    assert migrate_config.main(["--pools", str(legacy_package), "--apply"]) == 0

    pools = legacy_package / "pools"
    assert tree(legacy_package) == [
        "pools",
        "pools/default",
        "pools/default/.cursor.json",
        "pools/default/001.mp4",
        "pools/default/002.mp4",
        "pools/default/003.mp4",
        "pools/placeholder",
        "pools/placeholder/.cursor.json",
        "pools/placeholder/001.mp4",
        "pools/placeholder/002.mp4",
        "pools/placeholder/003.mp4",
        "pools/sleep",
        "pools/sleep/001.mkv",
    ]
    assert (pools / "default" / "001.mp4").read_text("utf-8") == "default 0"
    # The new pools resume exactly where the legacy cursors pointed.
    default = MediaPool(pools / "default")
    assert json.loads((default.directory / ".cursor.json").read_text("utf-8")) == {
        "index": 2,
        "seconds": 30,
    }
    assert MediaPool(pools / "placeholder").next_file().read_text("utf-8") == "judge 3"
    assert MediaPool(pools / "sleep").next_file().name == "001.mkv"


@pytest.mark.parametrize(
    ("next_index", "expected"), [(1, "judge 1"), (3, "judge 3"), (9, "judge 1")]
)
def test_placeholder_cursor_converts_from_one_based(
    migrate_config: ModuleType, legacy_package: Path, next_index: int, expected: str
):
    (legacy_package / "placeholder" / "state.json").write_text(
        json.dumps({"next_index": next_index}), encoding="utf-8"
    )

    migrate_config.migrate_pools(legacy_package, apply=True)

    pool = MediaPool(legacy_package / "pools" / "placeholder")
    assert pool.next_file().read_text("utf-8") == expected


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (lambda p: (p / "noise/default/001.mp4").unlink(), "without gaps"),
        (lambda p: (p / "noise/default/notes.txt").write_text("x"), "unexpected"),
        (lambda p: (p / "noise/default/state.json").write_text("{}x"), "cursor"),
        (lambda p: (p / "pools/sleep").mkdir(parents=True), "already exists"),
        (lambda p: shutil.copytree(p / "noise/sleep", p / "noise/placeholder"), "two"),
    ],
)
def test_pool_migration_refuses_before_moving_anything(
    migrate_config: ModuleType,
    legacy_package: Path,
    damage: Callable[[Path], object],
    message: str,
):
    damage(legacy_package)
    before = tree(legacy_package)

    with pytest.raises(ValueError, match=message):
        migrate_config.migrate_pools(legacy_package, apply=True)
    assert tree(legacy_package) == before
