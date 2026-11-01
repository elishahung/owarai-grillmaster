from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

import pytest
from tests.fakes import make_blocks, make_briefing

from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.core.model_spec import Role
from grillmaster.core.srt import SrtBlock, write_srt_file
from grillmaster.core.stage_key import SideTaskKey, StageKey
from grillmaster.core.timecode import format_timecode_line
from grillmaster.notes.annotate import (
    PROBE_TASK_NAME,
    TASK_NAME,
    NoteCandidate,
    NoteDraft,
    Notes,
    NotesAnswer,
    NotesProbe,
)
from grillmaster.notes.errors import NotesError
from grillmaster.pipeline.registry import NOTES_ONLY, NOTES_PROBE, STAGES
from grillmaster.project.state import SourceInfo
from grillmaster.project.store import load_state
from grillmaster.stages import notes
from grillmaster.stages.base import RunOptions
from grillmaster.telop.scan import TelopScan, TextRead
from grillmaster.telop.select import TelopSubtitle, TelopSubtitles

if TYPE_CHECKING:
    from tests.fakes import FakeAgentRunner
    from tests.stages.conftest import MakeContext

    from grillmaster.config.load import LoadedConfig
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState

CORNER = (0.02, 0.02, 0.3, 0.1)
CORNER_TEXT = "番組のロゴ"


class Waiter:
    def __init__(self) -> None:
        self.waited: list[SideTaskKey] = []

    def wait(self, key: SideTaskKey) -> None:
        self.waited.append(key)


@pytest.fixture
def script() -> dict[str, object]:
    return {
        TASK_NAME: NotesAnswer(
            notes=[NoteDraft(block=2, term="line 2", text="事件：說明", evidence="url")]
        ),
        PROBE_TASK_NAME: NotesProbe(
            candidates=[
                NoteCandidate(block=1, term="line 1", annotate=False, reason="已解釋"),
                NoteCandidate(block=2, term="第2句", annotate=True, reason="缺背景"),
            ]
        ),
    }


@pytest.fixture
def project(layout: ProjectLayout) -> ProjectLayout:
    """Voiced transcript, final subtitles, briefing, reports, a translated
    telop and a kept scan with a corner title up the whole time."""
    write_srt_file(layout.ja_srt, make_blocks(2))
    write_srt_file(layout.ja_voiced_srt, make_blocks(2))
    write_srt_file(
        layout.cht_srt,
        [
            SrtBlock(i, format_timecode_line(i * 2.0, i * 2.0 + 1.5), f"第{i}句")
            for i in (1, 2)
        ],
    )
    write_model(layout.prepass_briefing, make_briefing())
    layout.glossary_report.parent.mkdir(parents=True)
    layout.glossary_report.write_text("glossary findings", encoding="utf-8")
    write_model(
        layout.telop_cht_json,
        TelopSubtitles(
            items=[
                TelopSubtitle(
                    id=1,
                    start=1.0,
                    end=2.0,
                    ja="字幕",
                    zh="字卡翻譯",
                    boxes=[(0.4, 0.8, 0.6, 0.9)],
                    beside=[],
                )
            ]
        ),
    )
    reads = [TextRead(box=CORNER, text=CORNER_TEXT, score=0.99)]
    write_model(layout.telop_scan, TelopScan(fps=2.0, frames=[reads] * 20))
    return layout


def test_the_stage_runs_last():
    assert STAGES[-1] is notes.STAGE
    assert notes.STAGE.key is StageKey.NOTES
    step, compose = NOTES_ONLY.delivery
    assert (step.key, step.label) == (StageKey.NOTES.value, notes.STAGE.label)
    assert compose.key == "compose"
    assert NOTES_PROBE.delivery == (notes.PROBE,)


def _sections(
    *, feature: bool, series: bool | None = None, channel: bool | None = None
) -> dict[str, Any]:
    return {
        "features": {"notes": feature},
        "programs": {
            section: {name: {} if value is None else {"notes": value}}
            for section, name, value in (
                ("series", "かまいたちの掟", series),
                ("channel", "テレビ朝日", channel),
            )
        },
    }


@pytest.mark.parametrize(
    ("config_data", "flag", "expected"),
    [
        (_sections(feature=False), None, False),
        (_sections(feature=True), None, True),
        (_sections(feature=False, channel=True), None, True),
        (_sections(feature=True, series=False), None, True),
        (_sections(feature=False), True, True),
        (_sections(feature=True, series=True), False, False),
    ],
    indirect=["config_data"],
)
def test_enabled_by_the_flag_else_the_feature_or_any_notes_program(
    loaded: LoadedConfig, state: ProjectState, flag: bool | None, expected: bool
) -> None:
    options = RunOptions(source=state.source_id, notes=flag)
    assert notes.STAGE.enabled(options, loaded.config) is expected


@pytest.mark.parametrize(
    ("config_data", "flag", "expected"),
    [
        (_sections(feature=True, channel=False), None, False),
        (_sections(feature=False, series=True, channel=False), None, True),
        (_sections(feature=True, series=False, channel=True), None, False),
        (_sections(feature=False, series=False), True, True),
    ],
    indirect=["config_data"],
)
def test_applies_by_the_most_specific_choice(
    make_context: MakeContext, state: ProjectState, flag: bool | None, expected: bool
) -> None:
    state.source = SourceInfo(series="かまいたちの掟", channel="テレビ朝日")
    ctx = make_context(StageKey.NOTES)
    ctx.options = RunOptions(source=state.source_id, notes=flag)
    assert notes.STAGE.applies is not None
    assert notes.STAGE.applies(ctx) is expected


def test_writes_the_notes_from_the_final_subtitles_and_the_cards(
    make_context: MakeContext,
    project: ProjectLayout,
    state: ProjectState,
    agents: FakeAgentRunner,
) -> None:
    state.broadcast_date = date(2026, 10, 28)
    waiter = Waiter()

    assert notes.STAGE.run(make_context(StageKey.NOTES, waiter)) == "1 notes"

    assert waiter.waited == [SideTaskKey.TELOP_SCAN, SideTaskKey.DATE_RESEARCH]
    (note,) = read_model(project.notes_cht_json, Notes).items
    assert (note.block, note.start, note.text) == (2, 4.0, "事件：說明。")
    assert notes.STAGE.outputs(project) == (project.notes_cht_json,)
    task = agents.task(TASK_NAME)
    assert task.role is Role.EDITOR
    assert "Aired 2026-10-28." in task.prompt
    assert "2\t4.0\tline 2\t第2句" in task.prompt
    assert "字卡翻譯" in task.prompt
    assert CORNER_TEXT in task.prompt
    assert "## Earlier pass: glossary check\n\nglossary findings" in task.prompt
    assert "Earlier pass: proofread" not in task.prompt
    assert task.session_dir == project.work_dir(StageKey.NOTES) / "session"
    assert task.tools is not None
    assert task.tools.frames is not None
    assert task.tools.frames.frames_dir == project.notes_frames_dir


def test_without_a_scan_or_telops_the_notes_go_on_the_subtitles_alone(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
) -> None:
    project.telop_scan.unlink()
    project.telop_cht_json.unlink()

    assert notes.STAGE.run(make_context(StageKey.NOTES)) == "1 notes"

    prompt = agents.task(TASK_NAME).prompt
    assert "None: the viewer reads only the subtitles." in prompt
    assert "On-screen text" not in prompt
    assert "The air date is unknown." in prompt


def test_final_subtitles_that_do_not_pair_with_the_transcript_fail(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
) -> None:
    write_srt_file(project.cht_srt, make_blocks(1))

    with pytest.raises(NotesError, match="block by block"):
        notes.STAGE.run(make_context(StageKey.NOTES))
    assert agents.tasks == []


def test_grill_notes_writes_again_and_records_the_stage(
    make_context: MakeContext,
    project: ProjectLayout,
    state: ProjectState,
    agents: FakeAgentRunner,
) -> None:
    write_model(project.notes_cht_json, Notes())

    step, _ = NOTES_ONLY.delivery
    assert step.run(make_context(StageKey.NOTES)) == "1 notes"

    assert [task.name for task in agents.tasks] == [TASK_NAME]
    assert len(read_model(project.notes_cht_json, Notes).items) == 1
    assert load_state(project).is_done(StageKey.NOTES)


def test_the_probe_writes_what_would_be_noted_and_delivers_nothing(
    make_context: MakeContext,
    project: ProjectLayout,
    state: ProjectState,
    agents: FakeAgentRunner,
) -> None:
    assert notes.PROBE.run(make_context(StageKey.NOTES)) == "1/2 would be noted"

    task = agents.task(PROBE_TASK_NAME)
    assert task.requires == frozenset()
    assert task.session_dir == project.work_dir(StageKey.NOTES) / "session_probe"
    probe = read_model(project.notes_probe, NotesProbe)
    assert [c.annotate for c in probe.candidates] == [False, True]
    report = project.notes_probe_report.read_text(encoding="utf-8")
    assert "1 of 2 candidates would be noted." in report
    assert "| 2 | 4.0 | ✓ | 第2句 | 第2句 | 缺背景 |" in report
    assert not project.notes_cht_json.exists()
    assert not load_state(project).is_done(StageKey.NOTES)
