"""Persisted project state: `project.json`.

`ProjectState` is the single record of a project: identity, source metadata
the stages need, the section the video was cut to, metered spend, and the
stage ledger. The ledger is keyed by `StageKey`, so an unknown key (a renamed
or removed stage) fails validation instead of being silently ignored; old
projects are converted by the one-off migration script, never read here.

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

if TYPE_CHECKING:
    from collections.abc import Iterable


def now() -> datetime:
    """Timezone-aware local time, the stamp every persisted datetime uses."""
    return datetime.now().astimezone()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Talent(_Strict):
    """A person or group the source platform credits for the program."""

    id: str
    name: str
    name_kana: str | None = None
    roles: list[str] = Field(default_factory=list)


class SourceInfo(_Strict):
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


class Section(_Strict):
    """Source-timeline bounds (seconds) `video.mp4` was cut to; `None` = uncut."""

    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _check_order(self) -> Section:
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError(f"Section end {self.end} is not after start {self.start}")
        return self


class TaskRecord(_Strict):
    """Completion of a stage or side task."""

    completed_at: AwareDatetime
    elapsed_s: float = Field(ge=0)
    # Summed token counts of the task's agent sessions; `None` for tasks that
    # run no agent.
    agent_usage: Mapping[str, int] | None = None


class StageRecord(TaskRecord):
    params: Mapping[str, str] = Field(default_factory=dict)


class DateResearchRecord(TaskRecord):
    """Research outcome; the evidence lives in `work/side/date_research/`.

    A found date is applied to `ProjectState.broadcast_date`; `unknown` is
    recorded too so a resume does not pay for the research again.
    """

    verdict: Literal["found", "unknown"]
    trust: Literal["high", "medium", "low"] | None = None


class SideTasks(_Strict):
    cover: TaskRecord | None = None
    date_research: DateResearchRecord | None = None

    def is_done(self, key: SideTaskKey) -> bool:
        match key:
            case SideTaskKey.COVER:
                return self.cover is not None
            case SideTaskKey.DATE_RESEARCH:
                return self.date_research is not None


class ProjectState(_Strict):
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
    broadcast_date: date | None = None
    source: SourceInfo = Field(default_factory=SourceInfo)
    section: Section = Field(default_factory=Section)
    # ElevenLabs is the only metered service; agents run on subscriptions.
    asr_cost_usd: float = Field(default=0.0, ge=0)
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
        if amount_usd < 0:
            raise ValueError(f"ASR cost must be non-negative: {amount_usd}")
        self.asr_cost_usd += amount_usd
