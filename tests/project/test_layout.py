from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from grillmaster.core.stage_key import SideTaskKey, StageKey
from grillmaster.project.layout import ProjectLayout, session_dir

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path("projects/epabc123")


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


_NAMED_PATHS: list[tuple[Callable[[ProjectLayout], Path], str]] = [
    (lambda p: p.project_json, "project.json"),
    (lambda p: p.video, "video.mp4"),
    (lambda p: p.poster, "poster.jpg"),
    (lambda p: p.cover, "cover.png"),
    (lambda p: p.ja_srt, "subs/ja.srt"),
    (lambda p: p.ja_official_srt, "subs/ja.official.srt"),
    (lambda p: p.cht_srt, "video.cht.srt"),
    (lambda p: p.cht_ass, "video.cht.ass"),
    (lambda p: p.chat_cht_json, "subs/chat.cht.json"),
    (lambda p: p.logs_dir, "logs"),
    (lambda p: p.metadata_info, "work/01_metadata/info.json"),
    (lambda p: p.download_parts_dir, "work/02_download/parts"),
    (lambda p: p.full_video, "work/02_download/full.mp4"),
    (lambda p: p.chat_raw, "work/04_chat_fetch/live_chat.jsonl"),
    (lambda p: p.chat_messages, "work/04_chat_fetch/messages.json"),
    (lambda p: p.audio, "work/05_audio/audio.ogg"),
    (lambda p: p.asr_json, "work/06_asr/asr.json"),
    (lambda p: p.prepass_briefing, "work/08_prepass/briefing.json"),
    (lambda p: p.prepass_frames_dir, "work/08_prepass/frames"),
    (lambda p: p.merged_srt, "work/09_chunks/merged.srt"),
    (lambda p: p.refined_srt, "work/10_refine/refined.srt"),
    (lambda p: p.refine_report, "work/10_refine/report.md"),
    (lambda p: p.refine_frames_dir, "work/10_refine/frames"),
    (lambda p: p.glossary_checked_srt, "work/11_glossary/checked.srt"),
    (lambda p: p.glossary_briefing, "work/11_glossary/briefing.json"),
    (lambda p: p.glossary_report, "work/11_glossary/report.md"),
    (lambda p: p.glossary_frames_dir, "work/11_glossary/frames"),
    (lambda p: p.chat_batches_dir, "work/13_chat_translate/batches"),
    (lambda p: p.chat_polish, "work/13_chat_translate/polish.json"),
    (lambda p: p.date_research_result, "work/side/date_research/result.json"),
    (lambda p: p.package_work_dir, "work/package"),
    (lambda p: p.titles, "work/package/titles.json"),
    (lambda p: p.chat_panel_ass, "work/package/chat.ass"),
    (lambda p: p.side_dir(SideTaskKey.COVER), "work/side/cover"),
    (lambda p: p.side_dir(SideTaskKey.DATE_RESEARCH), "work/side/date_research"),
    (lambda p: p.chunk_dir(1, 119), "work/09_chunks/0001-0119"),
    (lambda p: p.chunk_frames_dir(1, 119), "work/09_chunks/0001-0119/frames"),
    (lambda p: p.chunk_audio(1, 119), "work/09_chunks/0001-0119/audio.ogg"),
    (
        lambda p: p.chunk_translation(120, 240),
        "work/09_chunks/0120-0240/translation.json",
    ),
    (lambda p: p.chat_batch(7), "work/13_chat_translate/batches/batch_0007.json"),
]


@pytest.mark.parametrize(
    ("path_of", "expected"),
    _NAMED_PATHS,
    ids=[expected for _, expected in _NAMED_PATHS],
)
def test_named_paths(path_of: Callable[[ProjectLayout], Path], expected: str):
    assert _rel(path_of(ProjectLayout(ROOT))) == expected


def test_every_stage_has_a_numbered_work_dir():
    layout = ProjectLayout(ROOT)
    names = [_rel(layout.work_dir(key)) for key in StageKey]
    assert names == [
        "work/01_metadata",
        "work/02_download",
        "work/03_combine",
        "work/04_chat_fetch",
        "work/05_audio",
        "work/06_asr",
        "work/07_transcript",
        "work/08_prepass",
        "work/09_chunks",
        "work/10_refine",
        "work/11_glossary",
        "work/12_finalize",
        "work/13_chat_translate",
    ]


def test_session_dirs_label_shared_parents():
    parent = Path("work/09_chunks/0001-0119")
    assert session_dir(parent) == parent / "session"
    assert session_dir(parent, label="polish") == parent / "session_polish"


def test_log_names_carry_the_run_start():
    layout = ProjectLayout(ROOT)
    started = datetime(2026, 10, 10, 9, 5, 7).astimezone()
    assert _rel(layout.run_log(started)) == "logs/run-20261010-090507.log"
    assert _rel(layout.events_log(started)) == "logs/events-20261010-090507.jsonl"


def test_agent_workspaces():
    assert [_rel(path) for path in ProjectLayout(ROOT).agent_workspaces] == [
        "work/10_refine",
        "work/11_glossary",
        "work/side/cover",
    ]


def test_session_and_frames_enumerations():
    layout = ProjectLayout(ROOT)
    assert [_rel(path) for path in layout.session_parents((1, 119))] == [
        "work/08_prepass",
        "work/09_chunks/0001-0119",
        "work/10_refine",
        "work/11_glossary",
        "work/13_chat_translate",
        "work/side/cover",
        "work/side/date_research",
        "work/package",
    ]
    assert [_rel(path) for path in layout.frames_dirs((1, 119))] == [
        "work/08_prepass/frames",
        "work/09_chunks/0001-0119/frames",
        "work/10_refine/frames",
        "work/11_glossary/frames",
    ]


def test_for_id_roots_the_project_under_the_projects_dir():
    assert ProjectLayout.for_id(Path("projects"), "epabc123").root == ROOT


def test_effective_briefing_prefers_the_glossary_copy(layout: ProjectLayout):
    assert layout.effective_briefing() == layout.prepass_briefing
    layout.glossary_briefing.parent.mkdir(parents=True)
    layout.glossary_briefing.write_text("{}", encoding="utf-8")
    assert layout.effective_briefing() == layout.glossary_briefing


def test_layout_never_creates_directories(tmp_path: Path):
    layout = ProjectLayout(tmp_path / "p")
    for key in StageKey:
        layout.work_dir(key)
    _ = (layout.chunk_frames_dir(1, 2), layout.titles, layout.effective_briefing())
    assert list(tmp_path.iterdir()) == []
