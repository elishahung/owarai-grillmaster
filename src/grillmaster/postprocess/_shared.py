"""What refine and glossary check share: their prompt package, naming the
files the agent writes, reading the input SRT and the skeleton check of an
agent-written SRT."""

from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.agents.errors import ValidationFailure
from grillmaster.core.srt import read_srt_file
from grillmaster.postprocess.errors import PostprocessError
from grillmaster.subtitles.structure import check_aligned, unreadable_problem

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.core.srt import SrtBlock


# Where refine and glossary check keep their prompt templates.
PROMPTS = "grillmaster.postprocess"


def workdir_name(path: Path, workdir: Path) -> str:
    """The bare name the prompt gives for a file the agent writes in its
    cwd; raises `ValueError` unless `path` lies directly in `workdir`."""
    if path.parent != workdir:
        raise ValueError(f"{path} is not directly in the agent workdir {workdir}")
    return path.name


def read_reference(path: Path, what: str) -> list[SrtBlock]:
    """The pass's input SRT; raises `PostprocessError` when it is missing."""
    if not path.is_file():
        raise PostprocessError(f"{what} missing: {path}")
    return read_srt_file(path)


def check_skeleton(
    reference: list[SrtBlock], reference_name: str, candidate: Path
) -> None:
    """`ValidationFailure` when the candidate file is unreadable, is not
    valid SRT or diverges from the reference skeleton."""
    try:
        blocks = read_srt_file(candidate)
    except OSError as error:
        problems = [unreadable_problem(candidate, error)]
    except ValueError as error:  # UnicodeDecodeError included
        raise ValidationFailure(
            f"`{candidate.name}` 不是有效的 SRT：{error}"
        ) from error
    else:
        problems = check_aligned(reference, blocks)
    if problems:
        raise ValidationFailure.from_problems(
            f"`{candidate.name}` 與 `{reference_name}` 的骨架不一致"
            "（區塊數、編號、時間碼必須完全相同，且每個區塊都要有文字）：",
            problems,
        )
