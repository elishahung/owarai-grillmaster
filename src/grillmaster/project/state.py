"""Persisted project state: `project.json`.

`ProjectState` is the single record of a project: identity, source metadata
the stages need, the section the video was cut to, metered spend, and the
stage ledger. Unknown fields are ignored (`StateModel`), but the ledger is
keyed by `StageKey`, so an unknown key (a renamed or removed stage) fails
validation instead of silently marking the stage undone.

Ledger `params` are a display snapshot (`grill status`), never a cache key.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from grillmaster.core.source_id import Platform, SourceId
from grillmaster.core.stage_key import SideTaskKey, StageKey
from grillmaster.core.talent import Talent

if TYPE_CHECKING:
    from collections.abc import Iterable


def now() -> datetime:
    """Timezone-aware local time, the stamp every persisted datetime uses."""
    return datetime.now().astimezone()


class StateModel(BaseModel):
    """Base of every `project.json` model: an unknown field (one a removed
    feature left behind) is ignored and dropped on the next save, since a
    stale key in a project's own record is harmless; a bad assignment still
    fails."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)


class SourceInfo(StateModel):
    """Program metadata the source platform published.

    `series` / `channel` select the program rules in `grill.toml`;
    `broadcast_label` is the raw on-air label (e.g. "2018年放送"), kept even
    when it yields no exact date because it is the only evidence of the
    original broadcast year.
    """

    title: str | None = None
    description: str | None = None
    series: str | None = None
    channel: str | None = None
    broadcast_label: str | None = None
    talents: list[Talent] = Field(default_factory=list)


class Section(StateModel):
    """Source-timeline bounds (seconds) `video.mp4` was cut to; `None` = uncut."""

    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _check_order(self) -> Section:
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError(f"Section end {self.end} is not after start {self.start}")
        return self

    @property
    def is_cut(self) -> bool:
        return self.start is not None or self.end is not None


class TaskRecord(StateModel):
    """Completion of a stage or side task."""

    completed_at: AwareDatetime
    elapsed_s: float = Field(ge=0)
    # Summed token counts of the task's agent sessions; `None` for tasks that
    # run no agent.
    agent_usage: Mapping[str, int] | None = None


class StageRecord(TaskRecord):
    params: Mapping[str, str] = Field(default_factory=dict)


class DateResearchRecord(TaskRecord):
    """Research outcome; the evidence lives in `.work/side/date_research/`.

    `broadcast_date` is the adopted date (`ProjectState.
    effective_broadcast_date` falls back to it); `unknown` is recorded too
    so a resume does not pay for the research again.
    """

    verdict: Literal["found", "unknown"]
    trust: Literal["high", "medium", "low"] | None = None
    broadcast_date: date | None = None


class SideTasks(StateModel):
    cover: TaskRecord | None = None
    date_research: DateResearchRecord | None = None
    telop: TaskRecord | None = None

    def is_done(self, key: SideTaskKey) -> bool:
        match key:
            case SideTaskKey.COVER:
                return self.cover is not None
            case SideTaskKey.DATE_RESEARCH:
                return self.date_research is not None
            case SideTaskKey.TELOP:
                return self.telop is not None

    def record(self, key: SideTaskKey, record: TaskRecord) -> None:
        """Store `record` as `key`'s completion (validated for the field)."""
        match key:
            case SideTaskKey.COVER:
                self.cover = record
            case SideTaskKey.DATE_RESEARCH:
                if not isinstance(record, DateResearchRecord):
                    raise TypeError("date research records a DateResearchRecord")
                self.date_research = record
            case SideTaskKey.TELOP:
                self.telop = record


class ProjectState(StateModel):
    id: str
    # Persisted rather than re-inferred: a bare ID is ambiguous across
    # platforms (`core.source_id.platform_of`).
    platform: Platform
    created_at: AwareDatetime
    # yt-dlp's file name for the video; `None` until the metadata stage.
    name: str | None = None
    translation_hint: str | None = None
    # Root of the parent project (possibly archived) whose briefing seeds this
    # one's pre-pass for cross-episode consistency.
    parent: Path | None = None
    # The platform's date (metadata stage, its only writer); readers use
    # `effective_broadcast_date`.
    broadcast_date: date | None = None
    source: SourceInfo = Field(default_factory=SourceInfo)
    section: Section = Field(default_factory=Section)
    # The metered services, cumulative; agents run on subscriptions.
    asr_cost_usd: float = Field(default=0.0, ge=0)  # ElevenLabs
    watch_clip_cost_usd: float = Field(default=0.0, ge=0)  # Meta, `watch_clip`
    stages: dict[StageKey, StageRecord] = Field(default_factory=dict)
    side_tasks: SideTasks = Field(default_factory=SideTasks)

    @classmethod
    def create(
        cls,
        source: SourceId,
        *,
        translation_hint: str | None = None,
        parent: Path | None = None,
    ) -> ProjectState:
        return cls(
            id=source.video_id,
            platform=source.platform,
            created_at=now(),
            translation_hint=translation_hint,
            parent=parent,
        )

    @property
    def source_id(self) -> SourceId:
        return SourceId(self.platform, self.id)

    @property
    def effective_broadcast_date(self) -> date | None:
        """The platform's date, else the one date research adopted."""
        if self.broadcast_date is not None:
            return self.broadcast_date
        research = self.side_tasks.date_research
        return research.broadcast_date if research is not None else None

    # --- ledger -------------------------------------------------------------

    def is_done(self, key: StageKey) -> bool:
        return key in self.stages

    def mark_done(
        self,
        key: StageKey,
        *,
        elapsed_s: float,
        params: Mapping[str, str] | None = None,
        agent_usage: Mapping[str, int] | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        """Record `key` as complete, replacing any earlier record."""
        self.stages[key] = StageRecord(
            completed_at=completed_at or now(),
            elapsed_s=elapsed_s,
            params=params or {},
            agent_usage=agent_usage,
        )

    def clear(self, keys: Iterable[StageKey]) -> None:
        """Forget `keys` (`grill reset`); keys not in the ledger are ignored."""
        for key in keys:
            self.stages.pop(key, None)

    def add_asr_cost(self, amount_usd: float) -> None:
        self.asr_cost_usd += _spend("ASR", amount_usd)

    def add_watch_clip_cost(self, amount_usd: float) -> None:
        self.watch_clip_cost_usd += _spend("watch_clip", amount_usd)


class _CompletionStamp(BaseModel):
    """The one ledger field a listing reads (see `ProjectSummary`)."""

    model_config = ConfigDict(extra="ignore")

    completed_at: AwareDatetime


class ProjectSummary(BaseModel):
    """The `project.json` fields a project listing shows; it reads the ledger
    by name, not `StageKey`, so one project with a stale stage key cannot
    hide the others."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str | None = None
    stages: dict[str, _CompletionStamp] = Field(default_factory=dict)

    @property
    def last_completed_at(self) -> datetime | None:
        """When the latest ledger stage completed; `None` before any did."""
        return max((s.completed_at for s in self.stages.values()), default=None)


def _spend(service: str, amount_usd: float) -> float:
    """`amount_usd`, refused when negative: spend only accumulates."""
    if amount_usd < 0:
        raise ValueError(f"{service} cost must be non-negative: {amount_usd}")
    return amount_usd
