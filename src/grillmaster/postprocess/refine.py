"""Refine: a medium polish of the translated Traditional Chinese subtitles.

The agent rewrites the merged chunk translation into `refined.srt` in its
stage directory (plus a short `report.md`), keeping the merged SRT's skeleton.
The skeleton check is the task validator, so a broken file is a repair round.
The report is asked for but not required (an optional output, so a stale one
never survives a fresh attempt).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from grillmaster.agents.task import AgentTask, FilesOutput
from grillmaster.core.model_spec import Role
from grillmaster.core.prompts import join_sections, render_program_instruction
from grillmaster.postprocess._shared import (
    check_skeleton,
    frames_prompt,
    read_reference,
    render_template,
    tool_session,
)

if TYPE_CHECKING:
    from grillmaster.core.tool_session import FramesTool

TASK_NAME = "refine"


@dataclass(frozen=True, slots=True)
class RefineInputs:
    """Everything the refine agent reads and writes.

    `translated_srt` is both the baseline and the skeleton reference. The two
    outputs sit in one directory, which becomes the agent's workdir.
    """

    translated_srt: Path
    ja_srt: Path
    briefing: Path
    prepass_frames_dir: Path
    chunks_dir: Path
    output_srt: Path
    report: Path
    # The program's configured refine text; empty when none.
    program_instruction: str = ""

    def __post_init__(self) -> None:
        if self.report.parent != self.output_srt.parent:
            raise ValueError(
                f"refine outputs must share one directory: {self.output_srt}, "
                f"{self.report}"
            )

    @property
    def workdir(self) -> Path:
        return self.output_srt.parent


def build_refine_task(
    inputs: RefineInputs,
    *,
    session_dir: Path,
    project_root: Path,
    frames: FramesTool,
) -> AgentTask[tuple[Path, ...]]:
    """The refine call; raises `PostprocessError` when the translation is missing.

    The project root is readable (`add_dirs`) so the absolute input paths in
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
            "refine.md",
            {
                "translated_srt": str(inputs.translated_srt),
                "ja_srt": str(inputs.ja_srt),
                "briefing": str(inputs.briefing),
                "prepass_frames_dir": str(inputs.prepass_frames_dir),
                "chunks_dir": str(inputs.chunks_dir),
                "output_srt": inputs.output_srt.name,
                "output_srt_path": str(inputs.output_srt),
                "report": inputs.report.name,
            },
        ),
        render_program_instruction(inputs.program_instruction),
    )
    return AgentTask(
        name=TASK_NAME,
        role=Role.POSTPROCESS,
        instructions=instructions,
        prompt=frames_prompt("refine_frames.md"),
        session_dir=session_dir,
        workdir=inputs.workdir,
        output=FilesOutput(
            (Path(inputs.output_srt.name),), optional=(Path(inputs.report.name),)
        ),
        tools=tool_session(project_root, frames, inputs.translated_srt),
        add_dirs=(project_root,),
        validate=validate,
    )
