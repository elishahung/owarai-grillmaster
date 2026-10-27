"""What the post-processing passes share: their prompt package, the
on-screen text section, naming the files the agent writes, session copies
of package files, reading the input SRT, the skeleton check of an
agent-written SRT and settling a corrected briefing."""

from __future__ import annotations

import shutil
from contextlib import contextmanager
from typing import TYPE_CHECKING

from grillmaster.agents.errors import ValidationFailure
from grillmaster.core.json_artifact import write_model
from grillmaster.core.prompts import render_template
from grillmaster.core.srt import read_srt_file
from grillmaster.postprocess.errors import PostprocessError
from grillmaster.subtitles.structure import check_aligned, unreadable_problem

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from grillmaster.core.briefing import Briefing
    from grillmaster.core.srt import SrtBlock


# Where refine and glossary check keep their prompt templates.
PROMPTS = "grillmaster.postprocess"


def telop_section(table: str | None) -> str | None:
    """The on-screen text reference section, when the project has one."""
    if table is None:
        return None
    return render_template(PROMPTS, "telop_reference.md", telops=table)


def workdir_name(path: Path, workdir: Path) -> str:
    """The bare name the prompt gives for a file the agent writes in its
    cwd; raises `ValueError` unless `path` lies directly in `workdir`."""
    if path.parent != workdir:
        raise ValueError(f"{path} is not directly in the agent workdir {workdir}")
    return path.name


def session_copy_paths(workdir: Path, *sources: Path) -> tuple[Path, ...]:
    """Where `session_copies` puts `sources`: by name, directly in `workdir`."""
    return tuple(workdir / source.name for source in sources)


@contextmanager
def session_copies(workdir: Path, *sources: Path) -> Iterator[None]:
    """Copy `sources` into `workdir` for one agent session (the package
    directory is never exposed to the agent); the copies are removed on exit,
    also when the session fails."""
    copies = session_copy_paths(workdir, *sources)
    try:
        for source, copy in zip(sources, copies, strict=True):
            shutil.copyfile(source, copy)
        yield
    finally:
        for copy in copies:
            copy.unlink(missing_ok=True)


def settle_briefing(corrected: Briefing | None, base: Briefing, dest: Path) -> bool:
    """Write `corrected` to `dest`, the pass's own briefing copy, when it
    differs from the `base` it read, else remove `dest`; so the copy exists
    exactly when an accepted run made a real fix. Returns whether it did."""
    if corrected is not None and corrected != base:
        write_model(dest, corrected)
        return True
    dest.unlink(missing_ok=True)
    return False


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
