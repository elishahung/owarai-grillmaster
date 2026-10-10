from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest
from tests.fakes import frames_tool, make_blocks

from grillmaster.agents.errors import ValidationFailure
from grillmaster.agents.task import FilesOutput
from grillmaster.core.model_spec import Role
from grillmaster.core.srt import serialize_srt, write_srt_file
from grillmaster.core.tool_session import SrtCheckTool, ToolSession
from grillmaster.postprocess.errors import PostprocessError
from grillmaster.postprocess.refine import (
    TASK_NAME,
    RefineInputs,
    build_refine_task,
)

REFERENCE = make_blocks(3)


@pytest.fixture
def inputs(tmp_path: Path) -> RefineInputs:
    stage = tmp_path / "work" / "10_refine"
    stage.mkdir(parents=True)
    translated = tmp_path / "work" / "09_chunks" / "merged.srt"
    write_srt_file(translated, REFERENCE)
    return RefineInputs(
        translated_srt=translated,
        ja_srt=tmp_path / "subs" / "ja.srt",
        briefing=tmp_path / "work" / "08_prepass" / "briefing.json",
        prepass_frames_dir=tmp_path / "work" / "08_prepass" / "frames",
        chunks_dir=tmp_path / "work" / "09_chunks",
        workdir=stage,
        output_srt=stage / "refined.srt",
        report=stage / "report.md",
    )


@pytest.fixture
def tools(inputs: RefineInputs, tmp_path: Path) -> ToolSession:
    return ToolSession(
        project_root=tmp_path,
        frames=frames_tool(tmp_path, tmp_path / "work" / "10_refine" / "frames"),
        check_srt=SrtCheckTool(reference_srt=inputs.translated_srt),
    )


def build(inputs: RefineInputs, tools: ToolSession):
    return build_refine_task(
        inputs,
        session_dir=inputs.workdir / "session",
        tools=tools,
    )


def test_outputs_outside_the_workdir_itself_are_refused(
    inputs: RefineInputs, tools: ToolSession
):
    # The prompt names outputs by bare file name, relative to the agent's cwd.
    nested = replace(inputs, report=inputs.workdir / "notes" / "report.md")
    with pytest.raises(ValueError, match="not directly in the agent workdir"):
        build(nested, tools)


def test_task_writes_in_the_stage_directory(inputs: RefineInputs, tools: ToolSession):
    task = build(inputs, tools)

    assert task.name == TASK_NAME
    assert task.role is Role.POSTPROCESS
    assert task.workdir == inputs.workdir
    assert task.output == FilesOutput(
        (Path("refined.srt"),), optional=(Path("report.md"),)
    )
    assert task.add_dirs == (tools.project_root,)
    assert task.tools is not None
    assert task.tools is tools
    assert task.tools.check_srt is not None
    assert task.tools.check_srt.reference_srt == inputs.translated_srt


def test_prompt_names_inputs_by_absolute_path(inputs: RefineInputs, tools: ToolSession):
    task = build(inputs, tools)

    for path in (
        inputs.translated_srt,
        inputs.ja_srt,
        inputs.briefing,
        inputs.prepass_frames_dir,
        inputs.output_srt,
    ):
        assert str(path) in task.instructions
    assert not re.search(r"\{[a-z_]+\}", task.instructions)
    assert "video.cht" not in task.instructions
    assert ".pre_pass" not in task.instructions
    assert "Refine is still a medium polishing pass" in task.prompt
    assert "`get_frames`" in task.prompt


def test_program_instruction_is_appended(inputs: RefineInputs, tools: ToolSession):
    plain = build(inputs, tools)
    ruled = build(replace(inputs, program_instruction="REFINE RULE"), tools)

    assert "REFINE RULE" not in plain.instructions
    assert ruled.instructions.startswith(plain.instructions)
    assert ruled.instructions.endswith("REFINE RULE")


def test_validator_accepts_a_rewrite_keeping_the_skeleton(
    inputs: RefineInputs, tools: ToolSession
):
    task = build(inputs, tools)
    rewritten = [replace(block, text=f"潤飾 {block.index}") for block in REFERENCE]
    # Codex sometimes writes a BOM.
    inputs.output_srt.write_text(serialize_srt(rewritten), encoding="utf-8-sig")

    assert task.validate is not None
    task.validate((inputs.output_srt,))


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param(
            serialize_srt(REFERENCE[:1]), "block count differs", id="truncated"
        ),
        pytest.param(
            serialize_srt([*REFERENCE[:2], replace(REFERENCE[2], text="")]),
            "block 3: text is empty",
            id="empty-block",
        ),
        pytest.param("1\nnot a timecode\n字\n", "不是有效的 SRT", id="malformed"),
        pytest.param(None, "cannot read", id="unreadable"),
    ],
)
def test_validator_rejects_a_broken_skeleton(
    inputs: RefineInputs,
    tools: ToolSession,
    content: str | None,
    expected: str,
):
    task = build(inputs, tools)
    if content is None:
        # A directory in its place: unreadable, like a locked file.
        inputs.output_srt.mkdir()
    else:
        inputs.output_srt.write_text(content, encoding="utf-8")

    assert task.validate is not None
    with pytest.raises(ValidationFailure, match=expected):
        task.validate((inputs.output_srt,))


def test_missing_translation_fails_before_any_agent(
    inputs: RefineInputs, tools: ToolSession
):
    inputs.translated_srt.unlink()

    with pytest.raises(PostprocessError, match="translated SRT"):
        build(inputs, tools)
