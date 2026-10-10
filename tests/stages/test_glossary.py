from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, Any

import pytest
from tests.fakes import FakeAgentRunner, make_blocks, make_briefing

from grillmaster.agents.errors import AgentOutputError
from grillmaster.core.briefing import Briefing
from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.core.srt import write_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.postprocess.glossary_check import TASK_NAME
from grillmaster.project.layout import session_dir
from grillmaster.stages import glossary

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tests.stages.conftest import MakeContext

    from grillmaster.agents.task import AgentTask
    from grillmaster.project.layout import ProjectLayout

BRIEFING = make_briefing()
CORRECTED = BRIEFING.model_copy(update={"summary": "fixed"})


@pytest.fixture
def project(layout: ProjectLayout) -> ProjectLayout:
    write_srt_file(layout.refined_srt, make_blocks(3))
    write_srt_file(layout.ja_srt, make_blocks(3))
    write_model(layout.prepass_briefing, BRIEFING)
    return layout


def agent_writing(
    briefing: Briefing | None = None, *, report: bool = True
) -> Callable[[AgentTask[Any]], tuple[Path, ...]]:
    def agent(task: AgentTask[Any]) -> tuple[Path, ...]:
        assert task.workdir is not None
        output = task.workdir / "checked.srt"
        shutil.copyfile(task.workdir.parent / "10_refine" / "refined.srt", output)
        if briefing is not None:
            write_model(task.workdir / "briefing.candidate.json", briefing)
        if report:
            (task.workdir / "report.md").write_text("# report\n", encoding="utf-8")
        return (output,)

    return agent


def test_checks_in_the_stage_directory(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
):
    agents.script[TASK_NAME] = agent_writing(report=False)

    glossary.STAGE.run(make_context(StageKey.GLOSSARY))

    assert project.glossary_checked_srt.read_bytes() == project.refined_srt.read_bytes()
    assert project.effective_briefing() == project.prepass_briefing
    task = agents.task(TASK_NAME)
    workdir = project.work_dir(StageKey.GLOSSARY)
    assert task.workdir == workdir
    assert task.session_dir == session_dir(workdir)
    assert task.add_dirs == (project.root,)
    assert task.tools is not None
    assert task.tools.frames is not None
    assert task.tools.frames.frames_dir == project.glossary_frames_dir
    assert task.tools.check_srt is not None
    assert task.tools.check_srt.reference_srt == project.refined_srt
    assert str(project.prepass_briefing) in task.instructions
    assert "Official CC reference" not in task.instructions


def test_a_briefing_correction_becomes_effective(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
):
    agents.script[TASK_NAME] = agent_writing(CORRECTED)

    glossary.STAGE.run(make_context(StageKey.GLOSSARY))

    assert project.effective_briefing() == project.glossary_briefing
    assert read_model(project.glossary_briefing, Briefing) == CORRECTED
    assert read_model(project.prepass_briefing, Briefing) == BRIEFING


def test_an_identical_briefing_copy_is_dropped(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
):
    agents.script[TASK_NAME] = agent_writing(BRIEFING, report=False)

    glossary.STAGE.run(make_context(StageKey.GLOSSARY))

    assert not project.glossary_briefing.exists()
    assert project.effective_briefing() == project.prepass_briefing


def test_an_unfinished_runs_outputs_are_discarded(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
):
    write_model(project.glossary_briefing_candidate, CORRECTED)
    write_model(project.glossary_briefing, CORRECTED)
    project.glossary_report.write_text("old", encoding="utf-8")
    project.glossary_checked_srt.write_text("old", encoding="utf-8")
    seen: list[tuple[bool, ...]] = []
    write = agent_writing(report=False)

    def agent(task: AgentTask[Any]) -> tuple[Path, ...]:
        seen.append(
            tuple(
                path.exists()
                for path in (
                    project.glossary_briefing_candidate,
                    project.glossary_report,
                    project.glossary_checked_srt,
                )
            )
        )
        assert str(project.prepass_briefing) in task.instructions
        return write(task)

    agents.script[TASK_NAME] = agent

    glossary.STAGE.run(make_context(StageKey.GLOSSARY))

    assert seen == [(False, False, False)]
    assert not project.glossary_briefing.exists()
    assert project.effective_briefing() == project.prepass_briefing


def test_a_rejected_correction_never_becomes_effective(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
):
    visible: list[Path] = []
    write = agent_writing(CORRECTED, report=False)

    def agent(task: AgentTask[Any]) -> tuple[Path, ...]:
        written = write(task)
        visible.append(project.effective_briefing())
        return written

    agents.script[TASK_NAME] = agent

    with pytest.raises(AgentOutputError):
        glossary.STAGE.run(make_context(StageKey.GLOSSARY))

    assert visible == [project.prepass_briefing]
    assert project.effective_briefing() == project.prepass_briefing


def test_official_captions_join_the_prompt(
    make_context: MakeContext, project: ProjectLayout, agents: FakeAgentRunner
):
    write_srt_file(project.ja_official_srt, make_blocks(1))
    agents.script[TASK_NAME] = agent_writing(report=False)

    glossary.STAGE.run(make_context(StageKey.GLOSSARY))

    instructions = agents.task(TASK_NAME).instructions
    assert "Official CC reference" in instructions
    assert str(project.ja_official_srt) in instructions
