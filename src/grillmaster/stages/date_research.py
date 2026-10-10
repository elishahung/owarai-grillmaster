"""Broadcast-date research side task: once the metadata stage has run, a
web-searching agent looks for the original on-air date the platform did not
give, keeping its verdict in `work/side/date_research/result.json`.

Runs when `--date-research` or `[features] date_research` asks for it, and
is moot once the project has a date or a recorded verdict. Either verdict
is recorded in `state.side_tasks.date_research` (a found date there, where
`ProjectState.effective_broadcast_date` reads it) so a resume never pays
twice. A verdict already paid for is applied even when the task is skipped
(disabled, or a `--break-after` run).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from grillmaster.core.model_spec import Role
from grillmaster.core.stage_key import SideTaskKey, StageKey
from grillmaster.events.types import SkipReason
from grillmaster.extras.date_research import (
    ResearchContext,
    adopted_date,
    load_cached_result,
    research_broadcast_date,
)
from grillmaster.project.state import DateResearchRecord, now
from grillmaster.sources.broadcast_date import parse_broadcast_label_year
from grillmaster.stages._common import flag_or_feature, role_params
from grillmaster.stages.base import SideTaskDef, StepOutcome

if TYPE_CHECKING:
    from datetime import date

    from grillmaster.extras.date_research import DateResearchResult
    from grillmaster.project.state import ProjectState
    from grillmaster.stages.base import StageContext


@dataclass(frozen=True, slots=True)
class _Verdict:
    result: DateResearchResult
    # The date to record, if the verdict found one (`adopted_date`).
    adopted: date | None


def _context(state: ProjectState) -> ResearchContext:
    source = state.source
    return ResearchContext(
        platform=state.platform.value,
        source_url=state.source_id.url,
        video_id=state.id,
        file_name=state.name,
        title=source.title,
        description=source.description,
        hint=state.translation_hint,
        broadcast_year=parse_broadcast_label_year(source.broadcast_label),
        broadcast_label=source.broadcast_label,
        talents=tuple(source.talents),
    )


def _verdict(ctx: StageContext, result: DateResearchResult) -> _Verdict:
    return _Verdict(
        result, adopted_date(result, evidence=ctx.layout.date_research_result)
    )


def _run(ctx: StageContext) -> _Verdict:
    result = research_broadcast_date(
        ctx.agents,
        _context(ctx.state),
        result_path=ctx.layout.date_research_result,
        session_dir=ctx.session_dir(),
    )
    return _verdict(ctx, result)


def _record(state: ProjectState, outcome: StepOutcome[_Verdict]) -> None:
    verdict = outcome.value
    state.side_tasks.date_research = DateResearchRecord(
        completed_at=now(),
        elapsed_s=outcome.elapsed,
        agent_usage=outcome.usage,
        verdict=verdict.result.status,
        trust=verdict.result.trust,
        broadcast_date=verdict.adopted,
    )


def _describe(verdict: _Verdict) -> str:
    result = verdict.result
    if result.broadcast_date is None:
        return result.status
    return f"{result.broadcast_date:%Y-%m-%d} ({result.trust} trust)"


def _is_done(state: ProjectState) -> bool:
    return (
        state.effective_broadcast_date is not None
        or state.side_tasks.date_research is not None
    )


def _on_skip(ctx: StageContext, reason: SkipReason) -> None:
    # Disabled and breakpoint skips come before the completion check.
    if reason is SkipReason.ALREADY_COMPLETE or _is_done(ctx.state):
        return
    cached = load_cached_result(ctx.layout.date_research_result)
    if cached is not None:
        outcome = StepOutcome(_verdict(ctx, cached), elapsed=0.0, usage=None)
        ctx.update(lambda state: _record(state, outcome))


TASK: SideTaskDef[_Verdict] = SideTaskDef(
    key=SideTaskKey.DATE_RESEARCH,
    label="Broadcast-date research",
    weight=1,
    start_after=StageKey.METADATA,
    run=_run,
    enabled=flag_or_feature("date_research"),
    record=_record,
    describe=_describe,
    is_done=_is_done,
    on_skip=_on_skip,
    params=role_params(Role.UTILITY, web_search="on"),
)
