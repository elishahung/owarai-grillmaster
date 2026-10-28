from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from tests.fakes import make_blocks, make_briefing
from tests.stages.conftest import complete_side_task

from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.core.srt import SrtBlock, write_srt_file
from grillmaster.core.stage_key import SideTaskKey, StageKey
from grillmaster.core.timecode import format_timecode_line
from grillmaster.pipeline.registry import STAGES, TELOP_ONLY
from grillmaster.project.state import SourceInfo
from grillmaster.project.store import load_state
from grillmaster.stages import _telop, telop_scan
from grillmaster.stages.base import RunOptions
from grillmaster.stages.telop import STAGE
from grillmaster.telop import ocr
from grillmaster.telop.errors import TelopError
from grillmaster.telop.scan import TelopCards, TelopScan, TextRead
from grillmaster.telop.select import (
    TASK_NAME,
    KeptTelop,
    TelopSelection,
    TelopSubtitles,
)

if TYPE_CHECKING:
    from tests.fakes import FakeAgentRunner
    from tests.stages.conftest import MakeContext

    from grillmaster.config.load import LoadedConfig
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState

CAPTION = (0.05, 0.85, 0.6, 0.95)
CAPTION_TEXT = "相変わらず何も知らない"


class Waiter:
    def __init__(self) -> None:
        self.waited: list[SideTaskKey] = []

    def wait(self, key: SideTaskKey) -> None:
        self.waited.append(key)


@pytest.fixture
def config_sections() -> dict[str, Any]:
    return {"programs": {"series": {"かまいたちの掟": {"telop": True}}}}


@pytest.fixture
def script() -> dict[str, object]:
    return {
        TASK_NAME: TelopSelection(
            kept=[KeptTelop(id=1, lines=[1], zh="還是什麼都不知道")]
        )
    }


@pytest.fixture
def project(layout: ProjectLayout) -> ProjectLayout:
    """Transcript, final subtitles, briefing and a kept scan: one caption
    shown for 2 s."""
    write_srt_file(layout.ja_srt, make_blocks(2))
    write_srt_file(
        layout.cht_srt,
        [
            SrtBlock(i, format_timecode_line(i * 2.0, i * 2.0 + 1.5), f"第{i}句")
            for i in (1, 2)
        ],
    )
    write_model(layout.prepass_briefing, make_briefing())
    reads = [TextRead(box=CAPTION, text=CAPTION_TEXT, score=0.99)]
    write_model(layout.telop_scan, TelopScan(fps=2.0, frames=[reads] * 4))
    return layout


def test_the_stage_runs_last_and_the_scan_after_combine() -> None:
    assert STAGES[-1] is STAGE
    assert STAGE.key is StageKey.TELOP
    assert STAGE.outputs is not None
    assert telop_scan.TASK.key is SideTaskKey.TELOP_SCAN
    assert telop_scan.TASK.start_after is StageKey.COMBINE
    (step,) = TELOP_ONLY.delivery
    assert (step.key, step.label) == (StageKey.TELOP.value, STAGE.label)


def test_enabled_by_the_flag_the_feature_or_any_telop_program(
    loaded: LoadedConfig, state: ProjectState
) -> None:
    config = loaded.config
    assert STAGE.enabled(RunOptions(source=state.source_id), config)
    no_program = config.model_copy(
        update={"programs": config.programs.model_copy(update={"series": {}})}
    )
    assert not STAGE.enabled(RunOptions(source=state.source_id), no_program)
    assert STAGE.enabled(RunOptions(source=state.source_id, telop=True), no_program)


def test_applies_only_to_the_programs_that_ask(
    make_context: MakeContext, state: ProjectState
) -> None:
    ctx = make_context(StageKey.TELOP)
    assert STAGE.applies is not None
    assert telop_scan.TASK.applies is _telop.applies
    assert not STAGE.applies(ctx)
    state.source = SourceInfo(series="かまいたちの掟")
    assert STAGE.applies(ctx)


def test_selects_beside_the_final_subtitles_after_the_scan(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
) -> None:
    waiter = Waiter()

    assert STAGE.run(make_context(StageKey.TELOP, waiter)) == "1 telops"

    assert waiter.waited == [SideTaskKey.TELOP_SCAN]
    subtitles = read_model(project.telop_cht_json, TelopSubtitles)
    assert [(item.start, item.end, item.ja, item.zh) for item in subtitles.items] == [
        (0.25, 1.75, CAPTION_TEXT, "還是什麼都不知道")
    ]
    cards = read_model(project.telop_cards, TelopCards)
    assert [card.text for card in cards.cards] == [CAPTION_TEXT]
    combined = project.cht_telop_ass.read_text(encoding="utf-8")
    assert "還是什麼都不知道" in combined
    assert combined.rstrip().endswith(",Default,,0,0,0,,第2句")
    assert project.cht_telop_ass in STAGE.outputs(project)
    task = agents.task(TASK_NAME)
    assert "line 1\t第1句" in task.prompt
    assert task.session_dir == project.work_dir(StageKey.TELOP) / "session"
    assert task.tools is not None
    assert task.tools.frames is not None
    assert task.tools.frames.frames_dir == project.telop_frames_dir


def test_final_subtitles_that_do_not_pair_with_the_transcript_fail(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
) -> None:
    write_srt_file(project.cht_srt, make_blocks(1))

    with pytest.raises(TelopError, match="block by block"):
        STAGE.run(make_context(StageKey.TELOP))

    assert agents.tasks == []


def test_the_preflight_wants_the_ocr_extra(
    loaded: LoadedConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    STAGE.preflight(loaded.config, loaded.secrets)
    monkeypatch.setattr(ocr, "_PACKAGES", ("grill_no_such_package",))

    with pytest.raises(TelopError, match="uv sync --extra telop"):
        STAGE.preflight(loaded.config, loaded.secrets)


def test_speech_like_cards_reach_the_selector_marked(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
) -> None:
    timecode = format_timecode_line(0.0, 2.0)
    write_srt_file(project.ja_srt, [SrtBlock(1, timecode, CAPTION_TEXT)])
    write_srt_file(project.cht_srt, [SrtBlock(1, timecode, "還是什麼都不知道")])

    STAGE.run(make_context(StageKey.TELOP))

    assert f"\t1\t[1] {CAPTION_TEXT}" in agents.task(TASK_NAME).prompt


def test_no_offered_card_writes_none_without_an_agent(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
) -> None:
    write_model(project.telop_scan, TelopScan(fps=2.0, frames=[]))

    assert STAGE.run(make_context(StageKey.TELOP)) == "0 telops"

    assert agents.tasks == []
    assert read_model(project.telop_cht_json, TelopSubtitles).items == []


def test_grill_telop_selects_again_and_records_the_stage(
    make_context: MakeContext,
    project: ProjectLayout,
    state: ProjectState,
    agents: FakeAgentRunner,
) -> None:
    state.mark_done(StageKey.TELOP, elapsed_s=1.0, params={}, agent_usage=None)
    write_model(project.telop_cht_json, TelopSubtitles())

    (step,) = TELOP_ONLY.delivery
    assert step.run(make_context(StageKey.TELOP)) == "1 telops"

    assert [task.name for task in agents.tasks] == [TASK_NAME]
    assert len(read_model(project.telop_cht_json, TelopSubtitles).items) == 1
    assert load_state(project).is_done(StageKey.TELOP)


def test_the_scan_task_reports_the_kept_scan(
    make_context: MakeContext, project: ProjectLayout, state: ProjectState
) -> None:
    ctx = make_context(SideTaskKey.TELOP_SCAN)

    assert complete_side_task(telop_scan.TASK, ctx) == "4 frames"
    assert load_state(project).side_tasks.telop_scan is not None
    assert state.side_tasks.is_done(SideTaskKey.TELOP_SCAN)


def test_the_reference_waits_for_the_scan_and_lists_the_offered_cards(
    make_context: MakeContext, project: ProjectLayout, state: ProjectState
) -> None:
    waiter = Waiter()
    ctx = make_context(StageKey.GLOSSARY, waiter)
    assert _telop.reference(ctx) is None
    assert waiter.waited == []

    state.source = SourceInfo(series="かまいたちの掟")
    reference = _telop.reference(ctx)

    assert waiter.waited == [SideTaskKey.TELOP_SCAN]
    assert reference == f"seconds\ttext\n0.2-1.8\t{CAPTION_TEXT}"


def test_a_missing_scan_leaves_no_reference(
    make_context: MakeContext, project: ProjectLayout, state: ProjectState
) -> None:
    project.telop_scan.unlink()
    state.source = SourceInfo(series="かまいたちの掟")

    assert _telop.reference(make_context(StageKey.PROOFREAD)) is None
