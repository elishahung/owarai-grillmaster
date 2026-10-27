from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from tests.fakes import make_blocks, make_briefing
from tests.stages.conftest import complete_side_task

from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.core.srt import write_srt_file
from grillmaster.core.stage_key import SideTaskKey, StageKey
from grillmaster.pipeline.registry import TELOP_ONLY
from grillmaster.project.state import SourceInfo
from grillmaster.project.store import load_state
from grillmaster.stages.base import RunOptions
from grillmaster.stages.telop import TASK
from grillmaster.telop.scan import TelopCards, TelopScan, TextRead
from grillmaster.telop.select import (
    TASK_NAME,
    KeptTelop,
    TelopSelection,
    TelopSubtitle,
    TelopSubtitles,
)

if TYPE_CHECKING:
    from tests.fakes import FakeAgentRunner
    from tests.stages.conftest import MakeContext

    from grillmaster.config.load import LoadedConfig
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState

CAPTION = (0.05, 0.85, 0.6, 0.95)


@pytest.fixture
def config_sections() -> dict[str, Any]:
    return {"programs": {"series": {"かまいたちの掟": {"telop": True}}}}


@pytest.fixture
def script() -> dict[str, object]:
    return {TASK_NAME: TelopSelection(kept=[KeptTelop(id=1, zh="還是什麼都不知道")])}


@pytest.fixture
def project(layout: ProjectLayout) -> ProjectLayout:
    """Transcript, briefing and a kept scan: one caption shown for 2 s."""
    write_srt_file(layout.ja_srt, make_blocks(2))
    write_model(layout.prepass_briefing, make_briefing())
    reads = [TextRead(box=CAPTION, text="相変わらず何も知らない", score=0.99)]
    write_model(layout.telop_scan, TelopScan(fps=2.0, frames=[reads] * 4))
    return layout


def test_starts_after_the_prepass() -> None:
    assert TASK.key is SideTaskKey.TELOP
    assert TASK.start_after is StageKey.PREPASS
    (step,) = TELOP_ONLY.delivery
    assert (step.key, step.label) == (SideTaskKey.TELOP.value, TASK.label)


def test_enabled_by_the_flag_the_feature_or_any_telop_program(
    loaded: LoadedConfig, state: ProjectState
) -> None:
    config = loaded.config
    assert TASK.enabled(RunOptions(source=state.source_id), config)
    no_program = config.model_copy(
        update={"programs": config.programs.model_copy(update={"series": {}})}
    )
    assert not TASK.enabled(RunOptions(source=state.source_id), no_program)
    assert TASK.enabled(RunOptions(source=state.source_id, telop=True), no_program)


def test_applies_only_to_the_programs_that_ask(
    make_context: MakeContext, state: ProjectState
) -> None:
    ctx = make_context(SideTaskKey.TELOP)
    assert TASK.applies is not None
    assert not TASK.applies(ctx)
    state.source = SourceInfo(series="かまいたちの掟")
    assert TASK.applies(ctx)


def test_selects_and_translates_the_scanned_cards(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
) -> None:
    assert complete_side_task(TASK, make_context(SideTaskKey.TELOP)) == "1 telops"

    subtitles = read_model(project.telop_cht_json, TelopSubtitles)
    assert [(item.start, item.end, item.ja, item.zh) for item in subtitles.items] == [
        (0.0, 2.0, "相変わらず何も知らない", "還是什麼都不知道")
    ]
    cards = read_model(project.telop_cards, TelopCards)
    assert [card.text for card in cards.cards] == ["相変わらず何も知らない"]
    task = agents.task(TASK_NAME)
    assert task.session_dir == project.side_dir(SideTaskKey.TELOP) / "session"
    assert task.tools is not None
    assert task.tools.frames is not None
    assert task.tools.frames.frames_dir == project.telop_frames_dir


def test_kept_telops_are_a_hit_without_an_agent(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
) -> None:
    kept = TelopSubtitles(
        items=[TelopSubtitle(id=1, start=0.0, end=2.0, ja="ja", zh="手改", box=CAPTION)]
    )
    write_model(project.telop_cht_json, kept)

    (step,) = TELOP_ONLY.delivery
    assert step.run(make_context(SideTaskKey.TELOP)) == "1 telops"

    assert agents.tasks == []
    assert read_model(project.telop_cht_json, TelopSubtitles) == kept
    # `grill telop` records the task like the pipeline does.
    assert load_state(project).side_tasks.telop is not None


def test_no_card_left_for_selection_writes_none_without_an_agent(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
) -> None:
    write_model(project.telop_scan, TelopScan(fps=2.0, frames=[]))

    assert complete_side_task(TASK, make_context(SideTaskKey.TELOP)) == "0 telops"

    assert agents.tasks == []
    assert read_model(project.telop_cht_json, TelopSubtitles).items == []
