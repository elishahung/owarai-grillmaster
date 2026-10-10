from __future__ import annotations

import json
import os
from datetime import date
from typing import TYPE_CHECKING, Any

import pytest

from grillmaster.core.briefing import Briefing, TermMapping
from grillmaster.core.paths import MAX_PATH_UNITS, measure
from grillmaster.core.source_id import Platform
from grillmaster.core.stage_key import StageKey
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.state import ProjectState

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

JA_SRT = """1
00:00:01,000 --> 00:00:02,000
こんにちは

2
00:00:02,500 --> 00:00:03,000
どうも

3
00:00:04,000 --> 00:00:05,000
はい

4
00:00:06,000 --> 00:00:07,000
以上です
"""

# Fails the structural check (block 2 missing); the fixed copy wins.
RAW_0001_0002 = """1
00:00:01,000 --> 00:00:02,000
你好
"""
FIXED_0001_0002 = """1
00:00:01,000 --> 00:00:02,000
你好

2
00:00:02,500 --> 00:00:03,000
多指教
"""
# Renumbered by the agent and led by a stray line; matched by timecode.
RAW_0003_0004 = """一行雜訊

1
00:00:04,000 --> 00:00:05,000
是

2
00:00:06,000 --> 00:00:07,000
就這樣
"""

BRIEFING = {
    "summary": "summary",
    "characters": [{"name_jp": "浜田", "name_zh": "濱田", "role_note": "MC"}],
    "proper_nouns": {"ダウンタウン": "Downtown"},
    "glossary": {"ツッコミ": "吐槽"},
    "catchphrases": [],
    "tone_notes": "tone",
    "segment_summaries": [{"from_index": 1, "to_index": 4, "summary": "s"}],
}
CORRECTED_BRIEFING = {**BRIEFING, "glossary": {"ツッコミ": "吐嘈"}}

INFO = {"title": "番組タイトル", "description": "番組説明"}


def _legacy_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "id": "epabc12345",
        "created_at": "2026-09-28T21:00:00.123456",
        "name": "番組_第1回",
        "translation_hint": "漫才特集",
        "parent_project_path": None,
        "broadcast_date": "2026-09-28",
        "source_metadata": {
            "talents": [
                {"id": "t1", "name": "浜田", "name_kana": "はまだ", "roles": ["MC"]}
            ],
            "series": "水曜日のダウンタウン",
            "channel": "TBS",
            "broadcast_date_label": "2026年放送",
            "title": "番組タイトル",
            "description": None,
        },
        "total_cost": 0.5,
        "service_costs": {"elevenlabs": 0.37, "gemini": 0.13},
        "section_start": None,
        "section_end": None,
        "is_metadata_fetched": True,
        "is_downloaded": True,
        "is_video_processed": True,
        "is_chat_fetched": True,
        "is_audio_processed": True,
        "is_asr_completed": True,
        "is_srt_completed": True,
        "is_prepass_completed": True,
        "is_chunk_translated": True,
        "is_srt_refined": True,
        "is_glossary_checked": True,
        "is_finalized": True,
        "is_chat_translated": True,
        "is_cover_generated": True,
        "is_broadcast_date_researched": True,
    }
    state.update(overrides)
    return state


def _write(root: Path, rel: str, content: str | bytes = "x") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=4)


def make_legacy_project(root: Path, **state_overrides: Any) -> Path:
    """A finished legacy project carrying every artifact kind the script maps."""
    files: dict[str, str | bytes] = {
        "project.json": _json(_legacy_state(**state_overrides)),
        "video.mp4": b"video",
        "poster.jpg": b"poster",
        "poster.cover.png": b"cover",
        "metadata.info.json": _json(INFO),
        "0.ja.srt": "caption",
        "video.ja.srt": JA_SRT,
        "video.official.ja.srt": "official",
        "video.cht.srt": "merged",
        "video.cht.refined.srt": "refined",
        "video.cht.glossary_checked.srt": "checked",
        "video.cht.finalized.srt": "finalized",
        "video.cht.ass": "ass",
        "chat.cht.json": "{}",
        "video.chat.ass": "chat ass",
        ".asr/audio.ogg": b"audio",
        ".asr/asr.json": "{}",
        ".pre_pass/pre_pass.raw.json": _json(BRIEFING),
        ".pre_pass/pre_pass.json": _json(CORRECTED_BRIEFING),
        ".pre_pass/assets.json": "{}",
        ".pre_pass/manifest.json": "{}",
        ".pre_pass/media/frames/frame_0000001.000_768.jpg": b"jpg",
        ".chunks/responses/chunk_0001-0002.raw.srt": RAW_0001_0002,
        ".chunks/responses/chunk_0001-0002.fixed.srt": FIXED_0001_0002,
        ".chunks/responses/chunk_0001-0002_fix/source.srt": "src",
        ".chunks/responses/chunk_0003-0004.raw.srt": RAW_0003_0004,
        ".chunks/manifests/chunk_0001-0002.json": "{}",
        ".chunks/media/audio/chunk_0001-0002.ogg": b"a",
        ".refine/report.md": "refine report",
        ".refine/extra_frames/frame_0000001.000_768.jpg": b"jpg",
        ".glossary_check/report.md": "glossary report",
        ".live_chat/live_chat.json": '{"a": 1}\n',
        ".live_chat/messages.json": '{"messages": []}',
        ".live_chat/polish.json": '{"corrections": []}',
        ".live_chat/batches/batch_0001.json": '{"translations": []}',
        ".artifacts/date_research.json": _json(
            {"status": "found", "broadcast_date": "2026-09-28", "trust": "high"}
        ),
        ".titles/titles.json": '{"titles": []}',
        "Thumbs.db": b"junk",
        # Not part of any known layout: must survive and be reported.
        "video.cht.v2.srt": "experiment",
        ".refine.bak/report.md": "backup",
    }
    for rel, content in files.items():
        _write(root, rel, content)
    return root


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    root = tmp_path / "archive"
    make_legacy_project(root / "26" / "09" / "260928_epabc12345_番組_第1回")
    return root


@pytest.fixture
def project(archive: Path) -> Path:
    return archive / "26" / "09" / "260928_epabc12345_番組_第1回"


def test_dry_run_writes_nothing_and_reports_the_plan(
    migrate_archive: ModuleType, archive: Path, capsys: pytest.CaptureFixture[str]
):
    before = snapshot(archive)

    exit_code = migrate_archive.main([str(archive)])

    assert exit_code == 0
    assert snapshot(archive) == before
    out = capsys.readouterr().out
    assert "260928_epabc12345_番組_第1回  [migrate]  epabc12345 (tver)" in out
    assert "unrecognised, left in place (2):" in out
    assert "     .refine.bak/report.md" in out
    assert "     video.cht.v2.srt" in out
    assert "chunk translations: 2 (from 3 response files)" in out
    assert "MAX_PATH violations: 0" in out


def test_apply_builds_the_new_layout(
    migrate_archive: ModuleType, archive: Path, project: Path
):
    layout = ProjectLayout(project)

    assert migrate_archive.main([str(archive), "--apply"]) == 0

    moved = {
        layout.ja_srt: JA_SRT,
        layout.ja_official_srt: "official",
        layout.cht_srt: "finalized",
        layout.cht_ass: "ass",
        layout.chat_cht_json: "{}",
        layout.merged_srt: "merged",
        layout.refined_srt: "refined",
        layout.refine_report: "refine report",
        layout.glossary_checked_srt: "checked",
        layout.glossary_report: "glossary report",
        layout.chat_raw: '{"a": 1}\n',
        layout.chat_messages: '{"messages": []}',
        layout.chat_polish: '{"corrections": []}',
        layout.chat_batch(1): '{"translations": []}',
        layout.chat_panel_ass: "chat ass",
        layout.titles: '{"titles": []}',
        layout.asr_json: "{}",
        layout.metadata_info: _json(INFO),
        layout.download_parts_dir / "0.ja.srt": "caption",
    }
    for path, content in moved.items():
        assert path.read_text(encoding="utf-8") == content, path
    assert layout.audio.read_bytes() == b"audio"
    assert layout.cover.read_bytes() == b"cover"
    assert layout.video.read_bytes() == b"video"
    assert layout.poster.read_bytes() == b"poster"
    assert json.loads(layout.date_research_result.read_text(encoding="utf-8"))[
        "trust"
    ] == ("high")

    # Unknown files stay; caches and emptied legacy directories are gone.
    assert (project / "video.cht.v2.srt").read_text(encoding="utf-8") == "experiment"
    assert (project / ".refine.bak" / "report.md").is_file()
    for gone in (".asr", ".pre_pass", ".chunks", ".refine", ".glossary_check",
                 ".live_chat", ".artifacts", ".titles", "Thumbs.db"):  # fmt: skip
        assert not (project / gone).exists(), gone
    assert (project / "logs" / "legacy-project.json").is_file()


def test_apply_converts_briefings_and_chunks(
    migrate_archive: ModuleType, archive: Path, project: Path
):
    layout = ProjectLayout(project)

    migrate_archive.main([str(archive), "--apply"])

    prepass = Briefing.model_validate_json(layout.prepass_briefing.read_text("utf-8"))
    assert prepass.proper_nouns == [
        TermMapping(source="ダウンタウン", target="Downtown")
    ]
    assert prepass.glossary == [TermMapping(source="ツッコミ", target="吐槽")]
    glossary = Briefing.model_validate_json(layout.glossary_briefing.read_text("utf-8"))
    assert glossary.glossary == [TermMapping(source="ツッコミ", target="吐嘈")]
    assert layout.effective_briefing() == layout.glossary_briefing

    assert json.loads(layout.chunk_translation(1, 2).read_text("utf-8")) == {
        "blocks": [{"index": 1, "text": "你好"}, {"index": 2, "text": "多指教"}]
    }
    assert json.loads(layout.chunk_translation(3, 4).read_text("utf-8")) == {
        "blocks": [{"index": 3, "text": "是"}, {"index": 4, "text": "就這樣"}]
    }


def test_apply_writes_a_valid_state(
    migrate_archive: ModuleType, archive: Path, project: Path
):
    migrate_archive.main([str(archive), "--apply"])

    state = ProjectState.model_validate_json(
        (project / "project.json").read_text("utf-8")
    )
    assert state.id == "epabc12345"
    assert state.platform is Platform.TVER
    assert state.created_at.tzinfo is not None
    # The legacy date came from research: the record owns it now.
    assert state.broadcast_date is None
    assert state.effective_broadcast_date == date(2026, 9, 28)
    assert state.translation_hint == "漫才特集"
    assert state.asr_cost_usd == pytest.approx(0.37)
    assert state.source.broadcast_label == "2026年放送"
    assert state.source.talents[0].name_kana == "はまだ"
    assert set(state.stages) == set(StageKey)
    assert all(record.elapsed_s == 0 for record in state.stages.values())
    assert state.side_tasks.cover is not None
    research = state.side_tasks.date_research
    assert research is not None
    assert (research.verdict, research.trust) == ("found", "high")
    assert research.broadcast_date == date(2026, 9, 28)


@pytest.mark.parametrize(
    "researched", [False, True], ids=["unresearched", "researched-unknown"]
)
def test_a_date_research_did_not_find_stays_the_platforms(
    migrate_archive: ModuleType, tmp_path: Path, *, researched: bool
):
    root = tmp_path / "archive"
    project = make_legacy_project(root / "p", is_broadcast_date_researched=researched)
    (project / ".artifacts" / "date_research.json").write_text(
        json.dumps({"status": "unknown"}), encoding="utf-8"
    )

    migrate_archive.main([str(root), "--apply"])

    state = ProjectState.model_validate_json(
        (project / "project.json").read_text("utf-8")
    )
    assert state.broadcast_date == date(2026, 9, 28)
    research = state.side_tasks.date_research
    assert research is None or research.broadcast_date is None


def test_apply_is_idempotent(migrate_archive: ModuleType, archive: Path, project: Path):
    migrate_archive.main([str(archive), "--apply"])
    after_first = snapshot(archive)

    assert migrate_archive.plan_project(project).status == "migrated"
    assert migrate_archive.main([str(archive), "--apply"]) == 0
    assert snapshot(archive) == after_first


def test_an_interrupted_apply_resumes(
    migrate_archive: ModuleType,
    archive: Path,
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    def crash(*_: object) -> None:
        raise OSError("network share dropped")

    with monkeypatch.context() as patch:
        patch.setattr(migrate_archive, "save_state", crash)
        assert migrate_archive.main([str(archive), "--apply"]) == 1

    assert migrate_archive.main([str(archive), "--apply"]) == 0

    layout = ProjectLayout(project)
    state = ProjectState.model_validate_json(layout.project_json.read_text("utf-8"))
    assert set(state.stages) == set(StageKey)
    # The pre-pass briefing written before the crash still backs the
    # glossary comparison, so the correction survives.
    assert layout.glossary_briefing.is_file()
    assert layout.chunk_translation(1, 2).is_file()


def test_legacy_hint_is_split_from_the_info_json(
    migrate_archive: ModuleType, tmp_path: Path
):
    root = make_legacy_project(
        tmp_path / "p",
        translation_hint=f"{INFO['title']} - {INFO['description']}",
        source_metadata={"series": "S", "channel": "C"},
        is_translated=True,
    )

    plan = migrate_archive.plan_project(root)

    assert plan.state.translation_hint is None
    assert plan.state.source.title == INFO["title"]
    assert plan.state.source.description == INFO["description"]


def test_conflicting_sources_block_the_project(
    migrate_archive: ModuleType, archive: Path, project: Path
):
    _write(project, "video.zh-hant.srt", "one-shot translation")
    before = snapshot(archive)

    assert migrate_archive.main([str(archive), "--apply"]) == 1

    assert snapshot(archive) == before
    plan = migrate_archive.plan_project(project)
    assert plan.blocked
    assert any("both map to work/09_chunks/merged.srt" in p for p in plan.problems)


def test_an_unconvertible_chunk_is_left_in_place(
    migrate_archive: ModuleType, archive: Path, project: Path
):
    (project / ".chunks/responses/chunk_0001-0002.fixed.srt").unlink()

    migrate_archive.main([str(archive), "--apply"])

    assert (project / ".chunks/responses/chunk_0001-0002.raw.srt").is_file()
    assert not ProjectLayout(project).chunk_translation(1, 2).exists()
    assert ProjectLayout(project).chunk_translation(3, 4).is_file()


def _response(first: str, second: str) -> str:
    return (
        f"1\n00:00:01,000 --> 00:00:02,000\n{first}\n\n"
        f"2\n00:00:02,500 --> 00:00:03,000\n{second}\n"
    )


def test_the_newest_valid_response_wins_regardless_of_fixed(
    migrate_archive: ModuleType, archive: Path, project: Path
):
    responses = project / ".chunks" / "responses"
    # An old round's fix, a newer round's raw, and a newest broken retry.
    candidates = {
        "chunk_0001-0002.fixed.srt": (_response("舊", "修正"), 1_000),
        "chunk_0001-0002.raw.srt": (_response("新", "版本"), 2_000),
        "chunk_0001-0002_abcdef12.raw.srt": (RAW_0001_0002, 3_000),
    }
    for name, (content, mtime) in candidates.items():
        _write(responses, name, content)
        os.utime(responses / name, (mtime, mtime))

    assert migrate_archive.main([str(archive), "--apply"]) == 0

    translation = ProjectLayout(project).chunk_translation(1, 2)
    assert json.loads(translation.read_text("utf-8"))["blocks"] == [
        {"index": 1, "text": "新"},
        {"index": 2, "text": "版本"},
    ]
    assert translation.stat().st_mtime == 2_000
    assert not responses.exists()


def test_a_write_that_reads_back_wrong_keeps_the_sources(
    migrate_archive: ModuleType,
    archive: Path,
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    def truncating_write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text[: len(text) // 2], encoding="utf-8", newline="\n")

    before = snapshot(archive)

    with monkeypatch.context() as patch:
        patch.setattr(migrate_archive, "atomic_write_text", truncating_write)
        assert migrate_archive.main([str(archive), "--apply"]) == 1

    # Nothing moved or deleted, and the truncated output is gone.
    assert snapshot(archive) == before
    assert migrate_archive.plan_project(project).status == "migrate"

    assert migrate_archive.main([str(archive), "--apply"]) == 0
    layout = ProjectLayout(project)
    Briefing.model_validate_json(layout.prepass_briefing.read_text("utf-8"))
    assert layout.chunk_translation(1, 2).is_file()


def test_an_error_in_one_project_does_not_stop_the_batch(
    migrate_archive: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    archive = tmp_path / "archive"
    bad = make_legacy_project(archive / "a_bad")
    good = make_legacy_project(archive / "b_good")
    build_state = migrate_archive._build_state

    def flaky_build_state(plan: Any, *args: Any) -> None:
        if plan.root == bad:
            raise RuntimeError("unexpected")
        build_state(plan, *args)

    monkeypatch.setattr(migrate_archive, "_build_state", flaky_build_state)

    assert migrate_archive.main([str(archive), "--apply"]) == 1

    out = capsys.readouterr().out
    assert f"FAILED: {bad}: RuntimeError: unexpected" in out
    assert "failed with an error: 1" in out
    assert "applied: 1" in out
    assert ProjectState.model_validate_json((good / "project.json").read_text("utf-8"))
    assert (bad / ".pre_pass" / "pre_pass.raw.json").is_file()


def test_only_legacy_part_names_count_as_download_parts(
    migrate_archive: ModuleType, project: Path
):
    for rel in ("0.mp4", "01.ja-JP.srt", "clip.mp4", "notes.srt", "0.en.srt",
                "sub/1.mp4"):  # fmt: skip
        _write(project, rel)

    plan = migrate_archive.plan_project(project)

    parts = ProjectLayout(project).download_parts_dir
    moved = {move.source.name: move.dest for move in plan.moves}
    assert moved["0.mp4"] == parts / "0.mp4"
    assert moved["01.ja-JP.srt"] == parts / "01.ja-JP.srt"
    assert moved["0.ja.srt"] == parts / "0.ja.srt"
    assert {"clip.mp4", "notes.srt", "0.en.srt", "sub/1.mp4"} <= set(plan.unknown)


def test_cache_and_junk_rules_apply_only_at_known_locations(
    migrate_archive: ModuleType, project: Path
):
    for rel in (".refine.bak/extra_frames/f.jpg", "misc/Thumbs.db",
                "extra_frames/f.jpg", ".chunks/responses/Thumbs.db"):  # fmt: skip
        _write(project, rel)

    plan = migrate_archive.plan_project(project)

    discarded = {
        discard.path.relative_to(project).as_posix(): discard.category
        for discard in plan.discards
    }
    assert discarded["extra_frames"] == "frame-tool cache"
    assert discarded[".refine/extra_frames"] == "frame-tool cache"
    assert discarded[".chunks/responses/Thumbs.db"] == "OS junk"
    assert discarded["Thumbs.db"] == "OS junk"
    assert {".refine.bak/extra_frames/f.jpg", "misc/Thumbs.db"} <= set(plan.unknown)


def _padded_root(tmp_path: Path, units: int) -> Path:
    return tmp_path / ("p" * (units - measure(str(tmp_path)) - 1))


def test_destinations_over_max_path_block_the_project(
    migrate_archive: ModuleType, tmp_path: Path
):
    # A root-level pre_pass.json grows by 16 units on its way into work/.
    root = _padded_root(tmp_path, MAX_PATH_UNITS - 19)
    make_legacy_project(root)
    (root / ".pre_pass" / "pre_pass.raw.json").unlink()
    (root / ".pre_pass" / "pre_pass.json").rename(root / "pre_pass.json")

    plan = migrate_archive.plan_project(root)

    assert plan.blocked
    long_paths = [path for path, _ in plan.long_paths]
    assert root / "work" / "08_prepass" / "briefing.json" in long_paths


def test_max_path_counts_the_atomic_write_temp_name(
    migrate_archive: ModuleType, tmp_path: Path
):
    briefing_rel = "/work/08_prepass/briefing.json"
    # The briefing itself fits; its `.briefing.json.XXXXXXXX.tmp` does not.
    root = _padded_root(tmp_path, MAX_PATH_UNITS - len(briefing_rel) - 5)
    make_legacy_project(root)

    plan = migrate_archive.plan_project(root)

    briefing = ProjectLayout(root).prepass_briefing
    assert measure(str(briefing)) <= MAX_PATH_UNITS
    assert (briefing, measure(str(briefing)) + 14) in plan.long_paths
    assert "project root too deep for future resumes" in dict(plan.warnings)


def test_unknown_patterns_collapse_digits_and_hashes(migrate_archive: ModuleType):
    assert (
        migrate_archive.unknown_pattern(
            ".chunks/responses/chunk_0102-0205_ab12cd34.zh.srt"
        )
        == ".chunks/responses/chunk_N-N_abNcdN.zh.srt"
    )
    assert (
        migrate_archive.unknown_pattern(
            "chunks/responses/chunk_0001-0042_ab12cd.raw.srt"
        )
        == "chunks/responses/chunk_N-N_<hash>.raw.srt"
    )


def test_the_legacy_full_video_lands_in_the_download_dir(
    migrate_archive: ModuleType, tmp_path: Path
):
    layout = ProjectLayout(tmp_path / "p")

    destinations = migrate_archive.move_destinations(layout)

    assert destinations["video.full.mp4"] == layout.full_video
