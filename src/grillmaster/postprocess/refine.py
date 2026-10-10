"""Refine: a medium polish of the translated Traditional Chinese subtitles.

The agent rewrites the merged chunk translation into `refined.srt` in its
stage directory (plus a short `report.md`), keeping the merged SRT's skeleton.
The skeleton check is the task validator, so a broken file is a repair round.
The report is asked for but not required (an optional output, so a stale one
never survives a fresh attempt).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from grillmaster.agents.task import AgentTask, FilesOutput
from grillmaster.core.model_spec import Role
from grillmaster.core.prompts import (
    frames_guidance,
    join_sections,
    load_prompt,
    render_program_instruction,
    render_template,
)
from grillmaster.postprocess._shared import (
    PROMPTS,
    check_skeleton,
    read_reference,
    workdir_name,
)

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.core.tool_session import ToolSession

TASK_NAME = "refine"


@dataclass(frozen=True, slots=True)
class RefineInputs:
    """Everything the refine agent reads and writes.

    `translated_srt` is both the baseline and the skeleton reference.
    `workdir` is the agent's cwd; the two outputs lie inside it.
    """

    translated_srt: Path
    ja_srt: Path
    briefing: Path
    prepass_frames_dir: Path
    chunks_dir: Path
    workdir: Path
    output_srt: Path
    report: Path
    # The program's configured refine text; empty when none.
    program_instruction: str = ""


def build_refine_task(
    inputs: RefineInputs,
    *,
    session_dir: Path,
    tools: ToolSession,
) -> AgentTask[tuple[Path, ...]]:
    """The refine call; raises `PostprocessError` when the translation is missing.

    `tools` offers `get_frames` and `check_srt` against the translated SRT.
    Its project root is readable (`add_dirs`) so the absolute input paths in
    the prompt resolve on every backend.
    """
    reference = read_reference(
        inputs.translated_srt, "translated SRT before refinement"
    )
    reference_name = inputs.translated_srt.name

    def validate(_paths: tuple[Path, ...]) -> None:
        check_skeleton(reference, reference_name, inputs.output_srt)

    instructions = join_sections(
        render_template(
            PROMPTS,
            "refine.md",
            translated_srt=str(inputs.translated_srt),
            ja_srt=str(inputs.ja_srt),
            briefing=str(inputs.briefing),
            prepass_frames_dir=str(inputs.prepass_frames_dir),
            chunks_dir=str(inputs.chunks_dir),
            output_srt=workdir_name(inputs.output_srt, inputs.workdir),
            output_srt_path=str(inputs.output_srt),
            report=workdir_name(inputs.report, inputs.workdir),
        ),
        render_program_instruction(inputs.program_instruction),
    )
    return AgentTask(
        name=TASK_NAME,
        role=Role.POSTPROCESS,
        instructions=instructions,
        prompt=frames_guidance(load_prompt(PROMPTS, "refine_frames.md")),
        session_dir=session_dir,
        workdir=inputs.workdir,
        output=FilesOutput(
            (inputs.output_srt.relative_to(inputs.workdir),),
            optional=(inputs.report.relative_to(inputs.workdir),),
        ),
        tools=tools,
        add_dirs=(tools.project_root,),
        validate=validate,
    )
