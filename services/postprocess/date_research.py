"""Agent-driven broadcast-date research fallback.

Runs only when the deterministic platform-metadata resolver
(`services.ytdlp.broadcast_date`) yielded no date. A web-searching agent
(the `agent_common_model` backend) researches the original Japanese broadcast
date and returns a schema-validated verdict; the full evidence report is
persisted to `.artifacts/date_research.json` for manual review. The worker
never touches `project.json` — the workflow applies the result at join time
via `apply_date_research_result`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from loguru import logger
from pydantic import BaseModel, Field, model_validator

from project import Project
from settings import settings
from services.inference import Backend, run_inference


_PROMPT = (Path(__file__).parent / "prompts" / "date_research.md").read_text(
    encoding="utf-8"
)


class DateResearchSource(BaseModel):
    """One source consulted as evidence for the reported date."""

    url: str
    source_name: str
    evidence_summary: str


class RejectedCandidate(BaseModel):
    """A candidate date found but rejected, with the reason."""

    date: str
    reason: str


class DateResearchResult(BaseModel):
    """Schema-enforced verdict of the broadcast-date research agent."""

    status: Literal["found", "unknown"]
    broadcast_date: date | None = None
    trust: Literal["high", "medium", "low"] | None = None
    sources: list[DateResearchSource] = Field(default_factory=list)
    rejected_candidates: list[RejectedCandidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_date_when_found(self) -> "DateResearchResult":
        # Feed a precise repair message back through the schema-enforcement
        # loop instead of accepting a half-filled "found" verdict.
        if self.status == "found":
            if self.broadcast_date is None:
                raise ValueError(
                    "broadcast_date is required when status is 'found'"
                )
            if self.trust is None:
                raise ValueError("trust is required when status is 'found'")
        return self


def _build_project_context(project: Project) -> str:
    """Format the persisted project fields as research seed material."""
    lines = [
        "## Project context",
        "",
        f"- Platform: {project.source.value}",
        f"- Source URL: {project.source_url}",
        f"- Video ID: {project.id}",
        f"- File name: {project.name}",
    ]
    if project.translation_hint:
        lines.append(f"- Title / description: {project.translation_hint}")
    talents_context = project.source_metadata_context()
    if talents_context:
        lines.append(f"- {talents_context}")
    return "\n".join(lines)


def load_cached_date_research(project: Project) -> DateResearchResult | None:
    """Load the persisted research result; a corrupt file counts as a miss.

    Treating an unreadable/invalid artifact as a miss (with a warning) lets
    the agent re-run and overwrite it instead of wedging every resume on the
    same parse error.
    """
    if not project.date_research_path.exists():
        return None
    try:
        return DateResearchResult.model_validate_json(
            project.date_research_path.read_text(encoding="utf-8")
        )
    except (ValueError, OSError) as error:
        logger.warning(
            f"Ignoring unreadable date research result "
            f"({project.date_research_path}): {error}"
        )
        return None


def research_broadcast_date(project: Project) -> DateResearchResult:
    """Research the original broadcast date with a web-searching agent.

    Cache hits on the fixed artifact filename: if
    `.artifacts/date_research.json` exists and parses, it is returned without
    invoking the agent (delete the file to force a re-run).
    """
    cached = load_cached_date_research(project)
    if cached is not None:
        logger.info(
            f"Date research result already exists, skipping agent "
            f"invocation: {project.date_research_path}"
        )
        return cached

    spec = settings.agent_common_model
    backend = Backend(spec.backend)
    logger.info(
        f"Invoking {backend.value} for broadcast-date research: {project.id}"
    )
    prompt = _PROMPT + "\n\n" + _build_project_context(project)
    # cwd is deliberately None (throwaway temp dir): this task must not touch
    # project files, and it runs concurrently with the cover agent which
    # already works inside the project dir.
    inference = run_inference(
        backend=backend,
        prompt=prompt,
        schema=DateResearchResult,
        model=spec.model,
        reasoning_effort=spec.reasoning_effort,
        web_search=True,
    )
    result = DateResearchResult.model_validate_json(inference.text)

    project.artifacts_dir.mkdir(parents=True, exist_ok=True)
    project.date_research_path.write_text(
        result.model_dump_json(indent=4), encoding="utf-8"
    )
    logger.info(f"Date research result saved: {project.date_research_path}")
    return result


def apply_date_research_result(
    project: Project, result: DateResearchResult
) -> None:
    """Apply a research verdict to the project (main-thread only).

    Writes `broadcast_date` for any found date but warns when the source
    trust is below high; marks the research done either way so a re-run does
    not re-invoke the agent.
    """
    if result.status == "found" and result.broadcast_date is not None:
        if result.trust != "high":
            source_names = (
                ", ".join(s.source_name for s in result.sources) or "none"
            )
            logger.warning(
                f"Broadcast date {result.broadcast_date:%Y-%m-%d} adopted "
                f"from {result.trust}-trust sources ({source_names}); "
                f"verify via {project.date_research_path}"
            )
        project.update_broadcast_date(result.broadcast_date)
        logger.info(
            f"Researched broadcast date: {result.broadcast_date:%Y-%m-%d} "
            f"(trust: {result.trust})"
        )
    else:
        logger.warning(
            "Broadcast date research found no reliable date; deliverables "
            f"will use the undated name (evidence: "
            f"{project.date_research_path})"
        )
    project.is_broadcast_date_researched = True
    project.save()
