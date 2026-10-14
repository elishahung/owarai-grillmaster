from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from tests.fakes import FakeAgentRunner, make_blocks, make_briefing

from grillmaster.config.model import validate_config
from grillmaster.core.json_artifact import write_model
from grillmaster.core.srt import read_srt_file, write_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.postprocess.review import (
    TASK_NAME,
    FindingCategory,
    ReviewAnswer,
    ReviewFinding,
)
from grillmaster.project.layout import session_dir
from grillmaster.stages import finalize, review
from grillmaster.stages.base import MissingArtifactError, RunOptions

if TYPE_CHECKING:
    from pathlib import Path

    from tests.stages.conftest import MakeContext

    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState

FIX = ReviewAnswer(
    findings=[
        ReviewFinding(
            block=2,
            current="line 2",
            revised=["第二行"],
            category=FindingCategory.MISTRANSLATION,
            reason="原因",
            evidence=None,
        )
    ],
    briefing_notes=[],
    summary="已核對。",
)


@pytest.fixture
def config_sections() -> dict[str, Any]:
    # Every block its own chunk: three seams.
    return {"translate": {"chunk_char_limit": 1}}


@pytest.fixture
def project(layout: ProjectLayout) -> ProjectLayout:
    for path in (
        layout.ja_srt,
        layout.merged_srt,
        layout.refined_srt,
        layout.glossary_checked_srt,
    ):
        write_srt_file(path, make_blocks(4))
    write_model(layout.prepass_briefing, make_briefing())
    return layout


def test_reviews_in_the_stage_directory(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
):
    agents.script[TASK_NAME] = FIX

    result = review.STAGE.run(make_context(StageKey.REVIEW))

    assert result == "1 findings"
    assert read_srt_file(project.reviewed_srt)[1].text == "第二行"
    assert project.effective_subtitles() == project.reviewed_srt
    assert project.review_report.is_file()
    assert project.review_findings.is_file()
    assert "after #1, after #2, after #3" in project.review_history.read_text("utf-8")
    task = agents.task(TASK_NAME)
    workdir = project.work_dir(StageKey.REVIEW)
    assert task.workdir == workdir
    assert task.session_dir == session_dir(workdir)
    assert task.tools is not None
    assert task.tools.frames is not None
    assert task.tools.frames.frames_dir == project.review_frames_dir
    assert task.tools.check_srt is None
    assert str(project.prepass_briefing) in task.instructions


def test_needs_the_glossary_check(make_context: MakeContext, project: ProjectLayout):
    project.glossary_checked_srt.unlink()

    with pytest.raises(MissingArtifactError, match="glossary"):
        review.STAGE.run(make_context(StageKey.REVIEW))


def test_skips_once_finalize_ran(
    make_context: MakeContext,
    project: ProjectLayout,
    state: ProjectState,
    agents: FakeAgentRunner,
):
    state.mark_done(StageKey.FINALIZE, elapsed_s=1.0)

    result = review.STAGE.run(make_context(StageKey.REVIEW))

    assert result == "skipped: finalize already ran"
    assert agents.tasks == []
    assert project.effective_subtitles() == project.glossary_checked_srt


def test_finalize_reads_the_reviewed_subtitles(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
):
    agents.script[TASK_NAME] = FIX
    review.STAGE.run(make_context(StageKey.REVIEW))

    finalize.STAGE.run(make_context(StageKey.FINALIZE))

    assert "第二行" in project.cht_srt.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("flag", "feature", "expected"),
    [(False, False, False), (True, False, True), (False, True, True)],
)
def test_runs_by_the_flag_or_the_feature(
    tmp_path: Path,
    state: ProjectState,
    roles: dict[str, str],
    *,
    flag: bool,
    feature: bool,
    expected: bool,
):
    config = validate_config(
        {"agents": {"roles": roles}, "features": {"review": feature}}, root=tmp_path
    )
    options = RunOptions(source=state.source_id, review=flag)

    assert review.STAGE.enabled(options, config) is expected
