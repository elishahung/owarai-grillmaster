from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from tests.pipeline.fakes import Journal, fake_stage

from grillmaster.core.stage_key import StageKey
from grillmaster.pipeline.registry import Pipeline
from grillmaster.pipeline.reset import reset, stages_from
from grillmaster.project.state import Section
from grillmaster.project.store import load_state

if TYPE_CHECKING:
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState


def forget_section(state: ProjectState) -> None:
    state.section = Section()


@pytest.fixture
def pipeline() -> Pipeline:
    journal = Journal()
    return Pipeline(
        (
            fake_stage(StageKey.COMBINE, journal, clear_state=forget_section),
            fake_stage(
                StageKey.TRANSCRIPT, journal, outputs=lambda layout: (layout.ja_srt,)
            ),
            fake_stage(StageKey.CHUNKS, journal),
            fake_stage(
                StageKey.FINALIZE,
                journal,
                outputs=lambda layout: (layout.cht_srt, layout.cht_ass),
            ),
        )
    )


@pytest.fixture
def finished(layout: ProjectLayout, state: ProjectState) -> ProjectState:
    """Every stage done with a work file, plus the declared deliverables."""
    for key in StageKey:
        state.mark_done(key, elapsed_s=1.0)
        layout.work_dir(key).mkdir(parents=True)
        (layout.work_dir(key) / "artifact.txt").write_text("x", encoding="utf-8")
    for output in (layout.ja_srt, layout.cht_srt, layout.cht_ass):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("x", encoding="utf-8")
    return state


def test_reset_from_clears_the_stage_and_everything_after(
    layout: ProjectLayout, finished: ProjectState, pipeline: Pipeline
):
    result = reset(layout, finished, stages_from(StageKey.GLOSSARY), pipeline=pipeline)
    cleared = (StageKey.GLOSSARY, StageKey.FINALIZE, StageKey.CHAT_TRANSLATE)
    assert result.stages == cleared
    assert result.removed == (
        layout.work_dir(StageKey.GLOSSARY),
        layout.work_dir(StageKey.FINALIZE),
        layout.cht_srt,
        layout.cht_ass,
        layout.work_dir(StageKey.CHAT_TRANSLATE),
    )
    saved = load_state(layout)
    assert [key for key in StageKey if saved.is_done(key)] == [
        key for key in StageKey if key not in cleared
    ]
    assert layout.work_dir(StageKey.REFINE).is_dir()
    assert layout.ja_srt.is_file()


def test_reset_only_leaves_downstream_alone(
    layout: ProjectLayout, finished: ProjectState, pipeline: Pipeline
):
    result = reset(layout, finished, (StageKey.TRANSCRIPT,), pipeline=pipeline)
    assert result.stages == (StageKey.TRANSCRIPT,)
    assert result.removed == (layout.work_dir(StageKey.TRANSCRIPT), layout.ja_srt)
    saved = load_state(layout)
    assert not saved.is_done(StageKey.TRANSCRIPT)
    assert saved.is_done(StageKey.PREPASS)
    assert layout.cht_srt.is_file()


def test_reset_of_a_stage_that_never_ran_removes_nothing(
    layout: ProjectLayout, state: ProjectState, pipeline: Pipeline
):
    assert reset(layout, state, (StageKey.CHUNKS,), pipeline=pipeline).removed == ()


def test_reset_clears_the_state_fields_a_stage_wrote(
    layout: ProjectLayout, finished: ProjectState, pipeline: Pipeline
):
    finished.section = Section(start=90.0)
    reset(layout, finished, (StageKey.COMBINE,), pipeline=pipeline)
    assert not load_state(layout).section.is_cut


def test_stages_from():
    assert stages_from(StageKey.FINALIZE) == (
        StageKey.FINALIZE,
        StageKey.CHAT_TRANSLATE,
    )


def test_on_reset_runs_after_the_save_and_before_any_deletion(
    layout: ProjectLayout, finished: ProjectState
):
    seen: list[tuple[bool, bool]] = []

    def on_reset(project: ProjectLayout) -> None:
        seen.append(
            (
                load_state(project).is_done(StageKey.COMBINE),
                project.work_dir(StageKey.CHUNKS).is_dir(),
            )
        )

    journal = Journal()
    pipeline = Pipeline(
        (
            replace(fake_stage(StageKey.COMBINE, journal), on_reset=on_reset),
            fake_stage(StageKey.CHUNKS, journal),
        )
    )

    reset(layout, finished, stages_from(StageKey.COMBINE), pipeline=pipeline)

    assert seen == [(False, True)]
