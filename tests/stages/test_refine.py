from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest
from tests.fakes import FakeAgentRunner, make_blocks

from grillmaster.agents.errors import AgentOutputError
from grillmaster.config.load import LoadedConfig
from grillmaster.config.model import ProgramsConfig
from grillmaster.core.briefing import Briefing
from grillmaster.core.json_artifact import write_model
from grillmaster.core.srt import read_srt_file, serialize_srt, write_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.postprocess.refine import TASK_NAME
from grillmaster.project.layout import session_dir
from grillmaster.project.state import SourceInfo
from grillmaster.stages import refine

if TYPE_CHECKING:
    from pathlib import Path

    from tests.stages.conftest import MakeContext

    from grillmaster.agents.task import AgentTask
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState

BLOCKS = make_blocks(3)
REFINED = [replace(block, text=f"潤飾 {block.index}") for block in BLOCKS]
BRIEFING = Briefing(
    summary="s",
    characters=[],
    proper_nouns=[],
    glossary=[],
    catchphrases=[],
    tone_notes="t",
    segment_summaries=[],
)


@pytest.fixture
def state(state: ProjectState) -> ProjectState:
    state.source = SourceInfo(channel="station")
    return state


@pytest.fixture
def loaded(loaded: LoadedConfig) -> LoadedConfig:
    programs = ProgramsConfig.model_validate(
        {
            "channel": {
                "station": {
                    "instruction": {"refine": "REFINE RULE", "chunks": "CHUNK RULE"}
                }
            }
        }
    )
    config = loaded.config.model_copy(update={"programs": programs})
    return LoadedConfig(root=loaded.root, config=config, secrets=loaded.secrets)


@pytest.fixture
def agents() -> FakeAgentRunner:
    return FakeAgentRunner()


@pytest.fixture
def project(layout: ProjectLayout) -> ProjectLayout:
    write_srt_file(layout.merged_srt, BLOCKS)
    write_srt_file(layout.ja_srt, BLOCKS)
    write_model(layout.prepass_briefing, BRIEFING)
    return layout


def writes(text: str, *, report: bool = True):
    def agent(task: AgentTask[Any]) -> tuple[Path, ...]:
        assert task.workdir is not None
        output = task.workdir / "refined.srt"
        output.write_text(text, encoding="utf-8")
        if report:
            (task.workdir / "report.md").write_text("| 字幕編號 |\n", encoding="utf-8")
        return (output,)

    return agent


def test_refines_into_the_stage_directory(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
):
    agents.script[TASK_NAME] = writes(serialize_srt(REFINED))

    refine.STAGE.run(make_context(StageKey.REFINE))

    assert read_srt_file(project.refined_srt) == REFINED
    assert project.refine_report.exists()
    task = agents.task(TASK_NAME)
    workdir = project.work_dir(StageKey.REFINE)
    assert task.workdir == workdir
    assert task.session_dir == session_dir(workdir)
    assert task.add_dirs == (project.root,)
    assert task.tools is not None
    assert task.tools.frames is not None
    assert task.tools.frames.frames_dir == project.refine_frames_dir
    assert task.tools.frames.window == (0.0, None)
    assert task.tools.check_srt is not None
    assert task.tools.check_srt.reference_srt == project.merged_srt
    assert str(project.prepass_briefing) in task.instructions
    assert "REFINE RULE" in task.instructions
    assert "CHUNK RULE" not in task.instructions


def test_an_unfinished_runs_outputs_are_discarded(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
):
    project.refined_srt.parent.mkdir(parents=True, exist_ok=True)
    project.refined_srt.write_text(serialize_srt(BLOCKS[:1]), encoding="utf-8")
    project.refine_report.write_text("old", encoding="utf-8")
    seen: list[bool] = []
    write = writes(serialize_srt(REFINED), report=False)

    def agent(task: AgentTask[Any]) -> tuple[Path, ...]:
        seen.append(project.refined_srt.exists() or project.refine_report.exists())
        return write(task)

    agents.script[TASK_NAME] = agent

    refine.STAGE.run(make_context(StageKey.REFINE))

    assert seen == [False]
    assert read_srt_file(project.refined_srt) == REFINED
    assert not project.refine_report.exists()


def test_a_broken_skeleton_fails_the_stage(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
):
    agents.script[TASK_NAME] = writes(serialize_srt(REFINED[:2]))

    with pytest.raises(AgentOutputError):
        refine.STAGE.run(make_context(StageKey.REFINE))


def test_declares_no_root_outputs(layout: ProjectLayout, loaded: LoadedConfig):
    assert refine.STAGE.key is StageKey.REFINE
    assert refine.STAGE.outputs(layout) == ()
    assert refine.STAGE.params(loaded.config) == {"model": "codex/gpt-5.6-sol/medium"}
