from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from tests.fakes import FakeAgentRunner, make_blocks, make_briefing

from grillmaster.config.model import validate_config
from grillmaster.core.briefing import Briefing
from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.core.model_spec import ModelSpec
from grillmaster.core.srt import read_srt_file, write_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.postprocess.proofread import (
    TASK_NAME,
    BriefingChange,
    BriefingField,
    BriefingPatch,
    Fix,
    FixCategory,
    ProofreadAnswer,
)
from grillmaster.project.layout import session_dir
from grillmaster.stages import finalize, proofread
from grillmaster.stages.base import MissingArtifactError, RunOptions

if TYPE_CHECKING:
    from pathlib import Path

    from tests.stages.conftest import MakeContext

    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState

FIX = ProofreadAnswer(
    fixes=[
        Fix(
            block=2,
            current="line 2",
            revised=["第二行"],
            category=FixCategory.MISTRANSLATION,
            reason="原因",
            evidence=None,
        )
    ],
    briefing=BriefingPatch.model_validate(dict.fromkeys(BriefingField)),
    briefing_changes=[],
    summary="已核對。",
)
CORRECTED = make_briefing(summary="corrected")
BRIEFING_FIX = FIX.model_copy(
    update={
        "briefing": FIX.briefing.model_copy(update={"summary": "corrected"}),
        "briefing_changes": [
            BriefingChange(
                field=BriefingField.SUMMARY,
                item="",
                reason="原因",
                evidence="frame_1.jpg",
            )
        ],
    }
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


def test_proofreads_in_the_stage_directory(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
):
    agents.script[TASK_NAME] = FIX

    result = proofread.STAGE.run(make_context(StageKey.PROOFREAD))

    assert result == "1 fixes, 0 briefing changes"
    assert read_srt_file(project.proofread_srt)[1].text == "第二行"
    assert project.effective_subtitles() == project.proofread_srt
    assert project.effective_briefing() == project.prepass_briefing
    assert project.proofread_report.is_file()
    assert project.proofread_answer.is_file()
    assert "after #1, after #2, after #3" in project.proofread_history.read_text(
        "utf-8"
    )
    task = agents.task(TASK_NAME)
    workdir = project.work_dir(StageKey.PROOFREAD)
    assert task.workdir == workdir
    assert task.session_dir == session_dir(workdir)
    assert task.tools is not None
    assert task.tools.frames is not None
    assert task.tools.frames.frames_dir == project.proofread_frames_dir
    assert task.tools.check_srt is None
    assert '"summary": "summary"' in task.prompt


PROOFREAD_TRANSCRIBE = {"features": {"proofread_transcribe": True}}


@pytest.mark.parametrize(
    "config_data",
    [{**PROOFREAD_TRANSCRIBE, "roles": {"transcribe": "gemini/gemini-3.8-flash/low"}}],
    indirect=True,
)
def test_the_feature_adds_the_transcribe_tool(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
):
    project.video.write_bytes(b"mp4")
    agents.script[TASK_NAME] = FIX

    proofread.STAGE.run(make_context(StageKey.PROOFREAD))

    tools = agents.task(TASK_NAME).tools
    assert tools is not None
    assert tools.transcribe is not None
    assert tools.transcribe.video == project.video
    assert tools.transcribe.clips_dir == project.proofread_transcribe_dir
    assert tools.transcribe.model == ModelSpec.parse("gemini/gemini-3.8-flash/low")
    assert tools.transcribe.timeout_s == 40 * 60.0
    assert "`transcribe(start, end)`" in agents.task(TASK_NAME).prompt


@pytest.mark.parametrize(
    ("features", "expected"),
    [({}, "off"), ({"proofread_transcribe": True}, "gemini/gemini-3.8-flash/low")],
)
def test_the_params_record_the_transcribe_model(
    tmp_path: Path,
    roles: dict[str, str],
    features: dict[str, bool],
    expected: str,
):
    config = validate_config(
        {
            "agents": {"roles": {**roles, "transcribe": "gemini/gemini-3.8-flash/low"}},
            "features": features,
        },
        root=tmp_path,
    )
    assert proofread.STAGE.params(config)["transcribe"] == expected


def test_without_the_feature_there_is_no_transcribe_tool(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
):
    agents.script[TASK_NAME] = FIX

    proofread.STAGE.run(make_context(StageKey.PROOFREAD))

    tools = agents.task(TASK_NAME).tools
    assert tools is not None
    assert tools.transcribe is None


def test_a_corrected_briefing_becomes_the_effective_one(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
):
    write_model(project.glossary_briefing, make_briefing(summary="checked"))
    agents.script[TASK_NAME] = BRIEFING_FIX

    result = proofread.STAGE.run(make_context(StageKey.PROOFREAD))

    assert result == "1 fixes, 1 briefing changes"
    assert project.effective_briefing() == project.proofread_briefing
    assert read_model(project.proofread_briefing, Briefing) == CORRECTED
    # It proofread the glossary check's copy.
    assert '"summary": "checked"' in agents.task(TASK_NAME).prompt


def test_a_rerun_reads_the_earlier_briefing_again(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
):
    agents.script[TASK_NAME] = BRIEFING_FIX
    proofread.STAGE.run(make_context(StageKey.PROOFREAD))
    agents.script[TASK_NAME] = FIX

    proofread.STAGE.run(make_context(StageKey.PROOFREAD))

    # Never an earlier attempt's own copy.
    assert '"summary": "summary"' in agents.tasks[-1].prompt
    assert not project.proofread_briefing.exists()
    assert project.effective_briefing() == project.prepass_briefing


def test_needs_the_glossary_check(make_context: MakeContext, project: ProjectLayout):
    project.glossary_checked_srt.unlink()

    with pytest.raises(MissingArtifactError, match="glossary"):
        proofread.STAGE.run(make_context(StageKey.PROOFREAD))


def test_skips_once_finalize_ran(
    make_context: MakeContext,
    project: ProjectLayout,
    state: ProjectState,
    agents: FakeAgentRunner,
):
    state.mark_done(StageKey.FINALIZE, elapsed_s=1.0)

    result = proofread.STAGE.run(make_context(StageKey.PROOFREAD))

    assert result == "skipped: finalize already ran"
    assert agents.tasks == []
    assert project.effective_subtitles() == project.glossary_checked_srt


def test_finalize_reads_the_proofread_subtitles(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
):
    agents.script[TASK_NAME] = FIX
    proofread.STAGE.run(make_context(StageKey.PROOFREAD))

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
        {"agents": {"roles": roles}, "features": {"proofread": feature}}, root=tmp_path
    )
    options = RunOptions(source=state.source_id, proofread=flag)

    assert proofread.STAGE.enabled(options, config) is expected
