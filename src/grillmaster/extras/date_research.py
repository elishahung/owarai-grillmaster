"""Broadcast-date research: a web-searching agent's fallback when the platform
metadata gave no date.

The agent works in a throwaway directory (it must not touch the project) and
returns a schema-checked verdict; the full verdict with its evidence is kept
at a fixed path for manual review and as the cache (an existing, parseable
file is a hit; a corrupt one is redone). Applying the verdict to the project
is the caller's job: `adopted_date` says which date, if any, to take.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Literal

from loguru import logger
from pydantic import Field, model_validator

from grillmaster.agents.adapters.base import Capability
from grillmaster.agents.task import AgentTask, SchemaOutput
from grillmaster.core.json_artifact import load_model, write_model
from grillmaster.core.model_spec import Role
from grillmaster.core.models import StrictModel
from grillmaster.core.prompts import load_prompt
from grillmaster.core.talent import render_talent_lines

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.agents.runner import AgentRunner
    from grillmaster.core.talent import Talent

TASK_NAME = "date_research"


class DateResearchSource(StrictModel):
    """One source consulted as evidence for the reported date."""

    url: str
    source_name: str
    evidence_summary: str


class RejectedCandidate(StrictModel):
    """A candidate date found but rejected, with the reason."""

    date: str
    reason: str


class DateResearchResult(StrictModel):
    """The research agent's verdict on the original broadcast date."""

    status: Literal["found", "unknown"]
    broadcast_date: date | None = None
    trust: Literal["high", "medium", "low"] | None = None
    sources: list[DateResearchSource] = Field(default_factory=list)
    rejected_candidates: list[RejectedCandidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_date_when_found(self) -> DateResearchResult:
        # A precise repair message instead of accepting a half-filled verdict.
        if self.status == "found":
            if self.broadcast_date is None:
                raise ValueError("broadcast_date is required when status is 'found'")
            if self.trust is None:
                raise ValueError("trust is required when status is 'found'")
        return self


@dataclass(frozen=True, slots=True)
class ResearchContext:
    """What the project knows about the episode: the research seed.

    `broadcast_year` is the year the platform's on-air label states
    (`broadcast_label`, e.g. "2018年放送"); it marks an archive re-upload.
    """

    platform: str
    source_url: str
    video_id: str
    file_name: str | None = None
    title: str | None = None
    description: str | None = None
    hint: str | None = None
    broadcast_year: int | None = None
    broadcast_label: str | None = None
    talents: tuple[Talent, ...] = ()


def render_context(context: ResearchContext) -> str:
    """The project fields as the `## Project context` block of the prompt."""
    lines = [
        "## Project context",
        "",
        f"- Platform: {context.platform}",
        f"- Source URL: {context.source_url}",
        f"- Video ID: {context.video_id}",
    ]
    if context.file_name:
        lines.append(f"- File name: {context.file_name}")
    if context.title:
        lines.append(f"- Title: {context.title}")
    if context.description:
        lines.append(f"- Description: {context.description}")
    if context.hint:
        lines.append(f"- User hint: {context.hint}")
    if context.broadcast_year is not None:
        lines.append(
            f"- Platform-stated original broadcast year: {context.broadcast_year} "
            f'(source label: "{context.broadcast_label}")'
        )
    if context.talents:
        lines.append("- Official source cast/talent metadata:")
        lines.extend(render_talent_lines(context.talents))
    return "\n".join(lines)


def build_date_research_task(
    context: ResearchContext, *, session_dir: Path
) -> AgentTask[DateResearchResult]:
    """The research call; no workdir, so the agent cannot touch the project."""
    return AgentTask(
        name=TASK_NAME,
        role=Role.UTILITY,
        instructions=load_prompt(__package__, "date_research.md"),
        prompt=render_context(context),
        session_dir=session_dir,
        workdir=None,
        output=SchemaOutput(DateResearchResult),
        requires=frozenset({Capability.WEB_SEARCH}),
    )


def load_cached_result(path: Path) -> DateResearchResult | None:
    """The kept verdict; `None` when missing or corrupt."""
    return load_model(path, DateResearchResult)


def research_broadcast_date(
    agents: AgentRunner,
    context: ResearchContext,
    *,
    result_path: Path,
    session_dir: Path,
) -> DateResearchResult:
    """The verdict at `result_path`, researching and writing it on a miss."""
    cached = load_cached_result(result_path)
    if cached is not None:
        logger.info(f"Date research result exists, skipping the agent: {result_path}")
        return cached

    logger.info(
        f"Invoking the research agent for the broadcast date: {context.video_id}"
    )
    result = agents.run(build_date_research_task(context, session_dir=session_dir))
    write_model(result_path, result.output)
    logger.info(f"Date research result saved: {result_path}")
    return result.output


def adopted_date(result: DateResearchResult, *, evidence: Path) -> date | None:
    """The date to apply, if the verdict found one.

    Any found date is taken, but one from sources below high trust is
    flagged for manual verification against `evidence`.
    """
    if result.status != "found" or result.broadcast_date is None:
        logger.warning(
            "Broadcast date research found no reliable date; deliverables will "
            f"use the undated name (evidence: {evidence})"
        )
        return None
    if result.trust != "high":
        source_names = ", ".join(s.source_name for s in result.sources) or "none"
        logger.warning(
            f"Broadcast date {result.broadcast_date:%Y-%m-%d} adopted from "
            f"{result.trust}-trust sources ({source_names}); verify via {evidence}"
        )
    logger.info(
        f"Researched broadcast date: {result.broadcast_date:%Y-%m-%d} "
        f"(trust: {result.trust})"
    )
    return result.broadcast_date
