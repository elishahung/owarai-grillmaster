"""Traditional Chinese title suggestions for a deliverable.

A packaging artifact, not a stage: the platform's public listing (program
names, official episode title and description, cast) and the episode's
briefing go to the `utility` agent, which returns the program's full Chinese
name and one candidate content per angle (`ANGLE_LABELS`): the official title
translated and trimmed, the project, a one-line summary, the guests and a
creative take; `project` is `None` when the episode has no named project.
Each carries a `basis` that names where it came from, never what happens:
the person picking a title has not watched the episode. The result is a
fixed-filename cache (`load_titles` / `generate_titles` take its path);
deleting the file forces a new run.

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
from grillmaster.core.talent import Talent

if TYPE_CHECKING:
    from datetime import date
    from pathlib import Path

    from grillmaster.agents.runner import AgentRunner

TASK_NAME = "titles"
# The angles in the order the deliverable lists them: `TitleSuggestions`
# field -> the label `info.json` shows.
ANGLE_LABELS = {
    "translation": "翻譯",
    "project": "企劃",
    "summary": "概述",
    "talent": "藝人",
    "creative": "創意",
}


class TitleCandidate(StrictModel):
    """One candidate content and where it came from. Each angle takes a
    subclass whose `title` bound reaches the model through the native schema
    (the `titles.md` table states the same lengths)."""

    title: str = Field(min_length=2)
    basis: str = Field(min_length=1)


class _Title10(TitleCandidate):
    title: str = Field(min_length=2, max_length=10)


class _Title12(TitleCandidate):
    title: str = Field(min_length=2, max_length=12)


class _Title14(TitleCandidate):
    title: str = Field(min_length=2, max_length=14)


class _Title24(TitleCandidate):
    title: str = Field(min_length=2, max_length=24)


class TitleSuggestions(StrictModel):
    """The title agent's program name and one candidate per angle; `project`
    is `None` when the episode has no named project."""

    program: str = Field(min_length=1)
    translation: _Title14
    project: _Title10 | None
    summary: _Title12
    talent: _Title24
    creative: _Title10

    def candidates(self) -> list[tuple[str, TitleCandidate]]:
        """`(label, candidate)` for each present angle, in `ANGLE_LABELS`
        order."""
        return [
            (label, candidate)
            for field, label in ANGLE_LABELS.items()
            if (candidate := getattr(self, field)) is not None
        ]


class SourceListing(StrictModel):
    """What the source platform published about the episode; any part may be
    unknown."""

    series: str | None = None
    channel: str | None = None
    title: str | None = None
    description: str | None = None
    talents: tuple[Talent, ...] = ()

    def render_for_prompt(self) -> str:
        """The listing as prompt JSON; talent ids are platform keys, noise to
        the agent."""
        return self.model_dump_json(indent=2, exclude={"talents": {"__all__": {"id"}}})


def load_titles(path: Path) -> TitleSuggestions | None:
    """The cached suggestions; `None` when missing or corrupt."""
    return load_model(path, TitleSuggestions)


def titles_info(
    suggestions: TitleSuggestions, *, nickname: str | None, aired: date | None
) -> dict[str, list[dict[str, str]]]:
    """The `info.json` titles: each candidate's angle, content, full
    deliverable title `<program>:<content> <YYMMDD>` (led by `nickname` when
    the program has one; an undated deliverable drops the date) and basis."""
    program = nickname or suggestions.program
    date_part = "" if aired is None else f" {aired:{AIR_DATE_FORMAT}}"
    return {
        "titles": [
            {
                "kind": label,
                "title": f"{program}:{candidate.title}{date_part}",
                "content": candidate.title,
                "basis": candidate.basis,
            }
            for label, candidate in suggestions.candidates()
        ]
    }


def build_titles_task(
    briefing: Briefing, *, source: SourceListing, session_dir: Path
) -> AgentTask[TitleSuggestions]:
    """The title call; the listing and briefing are the whole input, so no
    workdir."""
    prompt = join_sections(
        f"## source.json\n\n```json\n{source.render_for_prompt()}\n```",
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
    source: SourceListing,
    cache: Path,
    session_dir: Path,
) -> TitleSuggestions:
    """Run the title agent and write its suggestions to `cache`."""
    logger.info(f"Invoking the title agent: {cache}")
    result = agents.run(
        build_titles_task(briefing, source=source, session_dir=session_dir)
    )
    write_model(cache, result.output)
    logger.info(f"Title suggestions saved: {cache}")
    return result.output


def ensure_titles(
    agents: AgentRunner,
    *,
    briefing: Path,
    source: SourceListing,
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
            source=source,
            cache=cache,
            session_dir=session_dir,
        )
    except (AgentError, OSError, ValueError) as error:
        logger.warning(f"Title suggestion failed: {error}")
        return None
