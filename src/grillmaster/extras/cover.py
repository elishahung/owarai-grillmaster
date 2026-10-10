"""Cover: a cartoon reinterpretation of the poster, drawn by an image-generating agent.

The agent works in its own directory: the poster is staged there as
`poster.jpg` (the prompt's input name), the agent writes `cover.png` next to
it, and the finished file moves to the project's cover path. The task
requires `IMAGE_GENERATION`, so only an image-capable backend (codex) can
take the `image` role. An existing cover is a cache hit.

`copy_cover` is the packaging side: the best available cover image for a
deliverable directory.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.agents.adapters.base import Capability
from grillmaster.agents.task import AgentTask, FilesOutput
from grillmaster.core.model_spec import Role
from grillmaster.core.prompts import load_prompt
from grillmaster.extras.errors import ExtrasError

if TYPE_CHECKING:
    from grillmaster.agents.runner import AgentRunner

TASK_NAME = "cover"
# The file names the prompt speaks of, inside the agent's workdir.
POSTER_NAME = "poster.jpg"
COVER_NAME = "cover.png"


def build_cover_task(
    *, workdir: Path, session_dir: Path
) -> AgentTask[tuple[Path, ...]]:
    """The cover call over `workdir/poster.jpg`, which must already be staged."""
    poster = workdir / POSTER_NAME
    return AgentTask(
        name=TASK_NAME,
        role=Role.IMAGE,
        instructions=load_prompt(__package__, "cover.md"),
        # The instructions are the whole message, as they have always been.
        prompt="",
        session_dir=session_dir,
        workdir=workdir,
        output=FilesOutput((Path(COVER_NAME),)),
        images=(poster,),
        requires=frozenset({Capability.IMAGE_GENERATION}),
    )


def generate_cover(
    agents: AgentRunner,
    *,
    poster: Path,
    cover: Path,
    workdir: Path,
    session_dir: Path,
) -> None:
    """Make sure `cover` exists, drawing it from `poster` when it does not.

    Raises `ExtrasError` when the poster is missing and lets agent errors
    through.
    """
    if cover.exists():
        logger.info(f"Cover image already exists, skipping the agent: {cover}")
        return
    if not poster.is_file():
        raise ExtrasError(f"Poster missing, cannot draw a cover: {poster}")

    workdir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(poster, workdir / POSTER_NAME)
    logger.info(f"Invoking the image agent for the cover: {cover}")
    agents.run(build_cover_task(workdir=workdir, session_dir=session_dir))
    drawn = workdir / COVER_NAME
    if not drawn.is_file():
        raise ExtrasError(f"The cover agent left no {COVER_NAME}: {drawn}")
    drawn.replace(cover)
    logger.info(f"Cover image generated: {cover}")


def copy_cover(*, cover: Path, poster: Path, target_dir: Path) -> Path | None:
    """Copy the best cover into `target_dir`: the generated cover as
    `cover.png`, else the poster as `cover.jpg`; empty files do not count.

    Returns the copy, or `None` (with a warning) when neither exists.
    """
    for source, name in ((cover, "cover.png"), (poster, "cover.jpg")):
        if source.is_file() and source.stat().st_size > 0:
            target = target_dir / name
            shutil.copy2(source, target)
            logger.info(f"Copied cover: {source} -> {target}")
            return target
    logger.warning(f"No cover image found at {cover} or {poster}")
    return None
