from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from grillmaster.core.json_artifact import write_model
from grillmaster.core.srt import SrtBlock, write_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.core.timecode import format_timecode_line
from grillmaster.notes.annotate import Note, Notes
from grillmaster.pipeline.registry import DELIVERY
from grillmaster.stages import compose, package
from grillmaster.stages.base import MissingArtifactError, RunOptions
from grillmaster.telop.select import TelopSubtitle, TelopSubtitles

if TYPE_CHECKING:
    from tests.stages.conftest import MakeContext

    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState


@pytest.fixture
def project(layout: ProjectLayout) -> ProjectLayout:
    write_srt_file(
        layout.cht_srt, [SrtBlock(1, format_timecode_line(1.0, 2.0), "對白")]
    )
    write_model(
        layout.telop_cht_json,
        TelopSubtitles(
            items=[
                TelopSubtitle(
                    id=1,
                    start=1.0,
                    end=2.0,
                    ja="ja",
                    zh="字卡",
                    boxes=[(0.4, 0.8, 0.6, 0.9)],
                    beside=[],
                )
            ]
        ),
    )
    write_model(
        layout.notes_cht_json,
        Notes(
            items=[
                Note(block=1, start=1.0, end=4.0, term="對白", text="註", evidence="u")
            ]
        ),
    )
    return layout


def test_composes_before_packaging():
    assert DELIVERY == (compose.STEP, package.STEP)


def test_writes_the_dialogue_with_the_telops_and_the_notes(
    make_context: MakeContext, project: ProjectLayout
):
    result = compose.STEP.run(make_context(StageKey.FINALIZE))

    assert result == "1 lines, 1 telops, 1 notes"
    ass = project.cht_ass.read_text(encoding="utf-8")
    assert "字卡" in ass
    assert "※註" in ass
    assert ass.rstrip().endswith(",Default,,0,0,0,,對白")
    assert not project.package_work_dir.exists()


def test_left_out_layers_stay_out(
    make_context: MakeContext, project: ProjectLayout, state: ProjectState
):
    ctx = make_context(StageKey.FINALIZE)
    ctx.options = RunOptions(source=state.source_id, telop=False, notes=False)

    assert compose.STEP.run(ctx) == "1 lines"
    ass = project.cht_ass.read_text(encoding="utf-8")
    assert "字卡" not in ass
    assert "※" not in ass


def test_missing_layers_are_left_out(make_context: MakeContext, project: ProjectLayout):
    project.telop_cht_json.unlink()
    project.notes_cht_json.unlink()

    assert compose.STEP.run(make_context(StageKey.FINALIZE)) == "1 lines"


def test_needs_the_finalized_subtitles(
    make_context: MakeContext, project: ProjectLayout
):
    project.cht_srt.unlink()

    with pytest.raises(MissingArtifactError, match="--from finalize"):
        compose.STEP.run(make_context(StageKey.FINALIZE))
