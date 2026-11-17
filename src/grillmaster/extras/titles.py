"""Traditional Chinese title suggestions for a deliverable.

A packaging artifact, not a stage: the episode's briefing and the program's
source names go to the `utility` agent, which returns the program's full
Chinese name and exactly three candidate contents (a project, the guests or
what happens) with a one-line reason each. The result is a fixed-filename
cache (`load_titles` / `generate_titles` take its path); deleting the file
forces a new run.

The deliverable title is composed in code, `<program>:<content> <YYMMDD>`
(`titles_info`): the program part is the configured nickname when there is
one, else the agent's full name, so a nickname added later needs no new
agent run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger
from pydantic import Field

from grillmaster.agents.adapters.base import Capability
from grillmaster.agents.errors import AgentError
from grillmaster.agents.task import AgentTask, SchemaOutput
from grillmaster.core.air_date import AIR_DATE_FORMAT
from grillmaster.core.briefing import Briefing
from grillmaster.core.json_artifact import load_model, read_model, write_model
from grillmaster.core.model_spec import Role
from grillmaster.core.models import StrictModel
from grillmaster.core.prompts import join_sections, load_prompt

if TYPE_CHECKING:
    from datetime import date
    from pathlib import Path

    from grillmaster.agents.runner import AgentRunner

TASK_NAME = "titles"
# The deliverable always offers exactly three alternatives.
TITLE_COUNT = 3


class TitleSuggestion(StrictModel):
    """One candidate content with the reason it was chosen."""

    # The bounds reach the model through the native schema; pydantic still
    # rejects an over-long title, which becomes a repair round.
    title: str = Field(min_length=2, max_length=8)
    reason: str


class TitleSuggestions(StrictModel):
    """The title agent's program name and candidate contents."""

    program: str = Field(min_length=1)
    titles: list[TitleSuggestion] = Field(
        min_length=TITLE_COUNT, max_length=TITLE_COUNT
    )


class ProgramNames(StrictModel):
    """What the source platform calls the program; either may be unknown."""

    series: str | None = None
    channel: str | None = None


def load_titles(path: Path) -> TitleSuggestions | None:
    """The cached suggestions; `None` when missing or corrupt."""
    return load_model(path, TitleSuggestions)


def titles_info(
    suggestions: TitleSuggestions, *, nickname: str | None, aired: date | None
) -> dict[str, object]:
    """The `info.json` titles: each candidate as its full deliverable title,
    `<program>:<content> <YYMMDD>`, led by `nickname` when the program has
    one; an undated deliverable drops the date."""
    program = nickname or suggestions.program
    date_part = "" if aired is None else f" {aired:{AIR_DATE_FORMAT}}"
    return {
        "titles": [
            {
                "title": f"{program}:{suggestion.title}{date_part}",
                "reason": suggestion.reason,
            }
            for suggestion in suggestions.titles
        ]
    }


def build_titles_task(
    briefing: Briefing, *, program: ProgramNames, session_dir: Path
) -> AgentTask[TitleSuggestions]:
    """The title call; the briefing and program names are the whole input,
    so no workdir."""
    prompt = join_sections(
        f"## program.json\n\n```json\n{program.model_dump_json(indent=2)}\n```",
        f"## pre_pass.json\n\n```json\n{briefing.render_for_prompt()}\n```",
    )
    return AgentTask(
        name=TASK_NAME,
        role=Role.UTILITY,
        instructions=load_prompt(__package__, "titles.md"),
        prompt=prompt,
        session_dir=session_dir,
        workdir=None,
        output=SchemaOutput(TitleSuggestions),
        requires=frozenset({Capability.WEB_SEARCH}),
    )


def generate_titles(
    agents: AgentRunner,
    briefing: Briefing,
    *,
    program: ProgramNames,
    cache: Path,
    session_dir: Path,
) -> TitleSuggestions:
    """Run the title agent and write its suggestions to `cache`."""
    logger.info(f"Invoking the title agent: {cache}")
    result = agents.run(
        build_titles_task(briefing, program=program, session_dir=session_dir)
    )
    write_model(cache, result.output)
    logger.info(f"Title suggestions saved: {cache}")
    return result.output


def ensure_titles(
    agents: AgentRunner,
    *,
    briefing: Path,
    program: ProgramNames,
    cache: Path,
    session_dir: Path,
    enabled: bool,
) -> TitleSuggestions | None:
    """The suggestions a package run ships, or `None`.

    A cached result is reused whatever `enabled` says; a missing one is
    generated only when `enabled`. Best-effort like the rest of packaging: a
    missing briefing or a failed agent warns and yields `None`, never a
    broken deliverable.
    """
    cached = load_titles(cache)
    if cached is not None:
        logger.info(f"Title suggestions exist, skipping the agent: {cache}")
        return cached
    if not enabled:
        logger.info("Title suggestion disabled; packaging without titles")
        return None
    try:
        return generate_titles(
            agents,
            read_model(briefing, Briefing),
            program=program,
            cache=cache,
            session_dir=session_dir,
        )
    except (AgentError, OSError, ValueError) as error:
        logger.warning(f"Title suggestion failed: {error}")
        return None
