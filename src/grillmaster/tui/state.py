"""The dashboard's model of a run, rebuilt from events by a pure reducer.

`PipelineState.apply` folds one event into the model; nothing here imports
Textual or the pipeline. The plan (keys, labels, weights, params) comes from
`RunStarted`; agent sessions are keyed by task name, and the chunk board is
every session whose task name starts with `chunks/`.

The state is not locked: it is owned by the Textual thread, which applies
the events `TuiSink` queued on the emitting threads (each stamped there with
its time and step scope) before every render. `version` counts the changes,
so the app redraws only when it moved.
"""

from __future__ import annotations

import re
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import islice
from typing import TYPE_CHECKING, assert_never

from grillmaster.events.types import (
    ActivityKind,
    AgentActivity,
    AgentSessionFinished,
    AgentSessionStarted,
    BatchItemStarted,
    LogLine,
    PlanKind,
    ProgressAdvanced,
    ProgressFinished,
    ProgressStarted,
    RunFinished,
    RunOutcome,
    RunStarted,
    SessionOutcome,
    SkipReason,
    StepCompleted,
    StepFailed,
    StepSkipped,
    StepStarted,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from grillmaster.events.types import Event, PlanEntry

LOG_LINES_PER_SCOPE = 2000
ACTIVITY_LINES = 2000
ACTIVITY_LINES_PER_SESSION = 200

CHUNK_TASK_PREFIX = "chunks/"
_CHUNK_TASK = re.compile(r"^chunks/(\d+)-(\d+)$")

# Display order of the step list; plan order is kept within each kind.
KIND_ORDER = (PlanKind.STAGE, PlanKind.DELIVERY, PlanKind.SIDE_TASK)
# Kinds whose steps make up the header's overall progress.
_PROGRESS_KINDS = frozenset({PlanKind.STAGE, PlanKind.DELIVERY})


class RingLog[T]:
    """A bounded log that also counts every line ever appended, so a view
    can fetch just the lines it has not shown yet."""

    def __init__(self, maxlen: int) -> None:
        self._items: deque[T] = deque(maxlen=maxlen)
        self.count = 0

    def append(self, item: T) -> None:
        self._items.append(item)
        self.count += 1

    def since(self, seen: int) -> list[T]:
        """The lines appended after the first `seen`, as far as still kept."""
        fresh = min(self.count - seen, len(self._items))
        if fresh <= 0:
            return []
        # Walk from the newest end: O(fresh), not O(len).
        newest_first = list(islice(reversed(self._items), fresh))
        newest_first.reverse()
        return newest_first

    def __iter__(self) -> Iterator[T]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def last(self) -> T | None:
        return self._items[-1] if self._items else None


@dataclass(frozen=True, slots=True)
class LogEntry:
    level: str
    text: str


@dataclass(frozen=True, slots=True)
class ActivityEntry:
    task: str
    kind: ActivityKind
    summary: str


class ItemState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    CACHED = "cached"  # the ledger had it from an earlier run
    DISABLED = "disabled"  # turned off for this run
    SKIPPED = "skipped"  # never reached: the run stopped at --break-after
    FAILED = "failed"


@dataclass(slots=True)
class ProgressView:
    """One live progress scope; `total=None` is an indeterminate bar."""

    scope: str
    label: str
    total: float | None
    completed: float = 0.0
    note: str | None = None
    done: bool = False

    @property
    def fraction(self) -> float | None:
        if self.total is None:
            return None
        if self.total <= 0:
            return 0.0
        return min(1.0, self.completed / self.total)


@dataclass(slots=True)
class StepView:
    """One row of the step list: a stage, delivery step or side task."""

    key: str
    label: str
    kind: PlanKind
    params: Mapping[str, str]
    weight: int
    enabled: bool
    state: ItemState = ItemState.PENDING
    started_at: float | None = None
    elapsed: float = 0.0
    result: str | None = None
    error: str | None = None
    bars: dict[str, ProgressView] = field(default_factory=dict)
    log: RingLog[LogEntry] = field(
        default_factory=lambda: RingLog[LogEntry](LOG_LINES_PER_SCOPE)
    )

    def live_elapsed(self, now: float) -> float:
        if self.state is ItemState.RUNNING and self.started_at is not None:
            return now - self.started_at
        return self.elapsed


class SessionState(StrEnum):
    RUNNING = "running"
    OK = "ok"
    FAILED = "failed"


@dataclass(slots=True)
class SessionView:
    """An agent task's sessions; a retried task restarts the same view."""

    task: str
    stage: str | None
    backend: str
    model: str
    effort: str | None
    started_at: float
    # `(from, to)` of a chunk task (`chunk_range`), else `None`.
    span: tuple[int, int] | None = None
    attempts: int = 1
    state: SessionState = SessionState.RUNNING
    outcome: SessionOutcome | None = None
    elapsed: float = 0.0
    tool_calls: int = 0
    repairs: int = 0
    # Repairs of the attempts that already finished.
    repairs_before: int = 0
    usage: Counter[str] = field(default_factory=Counter[str])
    activity: RingLog[ActivityEntry] = field(
        default_factory=lambda: RingLog[ActivityEntry](ACTIVITY_LINES_PER_SESSION)
    )
    log: RingLog[LogEntry] = field(
        default_factory=lambda: RingLog[LogEntry](LOG_LINES_PER_SCOPE)
    )

    @property
    def spec(self) -> str:
        return f"{self.backend}/{self.model}" + (
            f"/{self.effort}" if self.effort else ""
        )

    @property
    def retries(self) -> int:
        return self.attempts - 1

    @property
    def last_activity(self) -> ActivityEntry | None:
        return self.activity.last()

    def live_elapsed(self, now: float) -> float:
        if self.state is SessionState.RUNNING:
            return now - self.started_at
        return self.elapsed


@dataclass(frozen=True, slots=True)
class ChunkCell:
    """A chunk-board cell: one `chunks/<from>-<to>` session (1-based block
    indices, as in the task name)."""

    from_index: int
    to_index: int
    session: SessionView


def chunk_range(task: str) -> tuple[int, int] | None:
    """`(from, to)` of a `chunks/0001-0119` task name; `None` for any other."""
    match = _CHUNK_TASK.match(task)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


@dataclass(frozen=True, slots=True)
class ChunkStats:
    total: int
    done: int
    active: int
    failed: int
    retries: int


class PipelineState:
    """The dashboard model. `clock` must match the one `TuiSink` stamps
    events with (both default to `time.monotonic`)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.project: str | None = None
        self.steps: list[StepView] = []
        self._by_key: dict[str, StepView] = {}
        self.sessions: dict[str, SessionView] = {}
        self.activity = RingLog[ActivityEntry](ACTIVITY_LINES)
        # Lines logged outside any step (project load, run errors).
        self.pipeline_log = RingLog[LogEntry](LOG_LINES_PER_SCOPE)
        self.batch: tuple[int, int] | None = None
        self.run_outcome: RunOutcome | None = None
        self.started_at = clock()
        self.finished_at: float | None = None
        # The work handed to `run_with_tui` returned or raised.
        self.finished = False
        self.failed = False
        self.error: str | None = None
        self.current_step_key: str | None = None
        self._bar_owner: dict[str, str] = {}
        # Bumped by every change; the app redraws when it moved.
        self.version = 0

    # -- reading -------------------------------------------------------------

    def now(self) -> float:
        return self._clock()

    def step(self, key: str) -> StepView | None:
        return self._by_key.get(key)

    def display_steps(self) -> list[StepView]:
        """Steps in list order: stages, delivery, side tasks."""
        return sorted(self.steps, key=lambda step: KIND_ORDER.index(step.kind))

    def sessions_for(self, key: str) -> list[SessionView]:
        return [session for session in self.sessions.values() if session.stage == key]

    def chunk_cells(self, key: str) -> list[ChunkCell]:
        """The chunk board of step `key`, in block order."""
        cells = [
            ChunkCell(*session.span, session)
            for session in self.sessions_for(key)
            if session.span is not None
        ]
        return sorted(cells, key=lambda cell: cell.from_index)

    def chunk_stats(self, key: str) -> ChunkStats:
        cells = self.chunk_cells(key)
        states = Counter(cell.session.state for cell in cells)
        return ChunkStats(
            total=len(cells),
            done=states[SessionState.OK],
            active=states[SessionState.RUNNING],
            failed=states[SessionState.FAILED],
            retries=sum(cell.session.retries for cell in cells),
        )

    def step_progress(self, step: StepView) -> float:
        if step.state in {ItemState.DONE, ItemState.CACHED}:
            return 1.0
        if step.state is not ItemState.RUNNING:
            return 0.0
        stats = self.chunk_stats(step.key)
        if stats.total:
            return stats.done / stats.total
        fractions = [
            fraction
            for bar in step.bars.values()
            if not bar.done and (fraction := bar.fraction) is not None
        ]
        return max(fractions, default=0.0)

    def total_progress(self) -> float:
        """Weighted progress over the stages and delivery steps that run."""
        total = done = 0.0
        for step in self.steps:
            if step.kind not in _PROGRESS_KINDS or step.state in {
                ItemState.DISABLED,
                ItemState.SKIPPED,
            }:
                continue
            total += step.weight
            done += step.weight * self.step_progress(step)
        return done / total if total > 0 else 0.0

    def wall_elapsed(self) -> float:
        end = self.finished_at if self.finished_at is not None else self._clock()
        return end - self.started_at

    def total_usage(self) -> Counter[str]:
        usage = Counter[str]()
        for session in self.sessions.values():
            usage.update(session.usage)
        return usage

    # -- reducing ------------------------------------------------------------

    def apply(
        self, event: Event, *, at: float | None = None, stage: str | None = None
    ) -> None:
        """Fold `event` in. `at` is when it was emitted (default: now) and
        `stage` the step scope it was emitted in, which attributes progress
        bars and agent sessions that carry no step of their own."""
        at = self._clock() if at is None else at
        self.version += 1
        match event:
            case RunStarted(project=project, plan=plan):
                self._start_run(project, plan)
            case RunFinished(outcome=outcome, error=error):
                self._finish_run(outcome, error, at)
            case BatchItemStarted(index=index, total=total, source=source):
                self.batch = (index, total)
                self.pipeline_log.append(
                    LogEntry("INFO", f"Serial {index}/{total}: {source}")
                )
            case StepStarted(key=key, kind=kind):
                step = self._ensure_step(key, kind)
                step.state = ItemState.RUNNING
                step.started_at = at
                step.error = None
                if kind is not PlanKind.SIDE_TASK:
                    self.current_step_key = key
            case StepCompleted(key=key, kind=kind, elapsed=elapsed, result=result):
                step = self._ensure_step(key, kind)
                step.state = ItemState.DONE
                step.elapsed = elapsed
                step.result = result
                self._leave(key)
            case StepSkipped(key=key, kind=kind, reason=reason):
                self._ensure_step(key, kind).state = _SKIP_STATE[reason]
            case StepFailed(key=key, kind=kind, error=error):
                self._fail_step(self._ensure_step(key, kind), error, at)
                self._leave(key)
            case ProgressStarted() | ProgressAdvanced() | ProgressFinished():
                self._progress(event, stage)
            case AgentSessionStarted():
                self._session_started(event, at, stage)
            case AgentActivity():
                self._activity(event)
            case AgentSessionFinished():
                self._session_finished(event)
            case LogLine():
                self._log(event)
            case _:
                assert_never(event)

    def work_finished(self, error: str | None, *, at: float | None = None) -> None:
        """The work returned (`error=None`) or raised; the dashboard is done."""
        at = self._clock() if at is None else at
        self.version += 1
        self.finished = True
        self.finished_at = at
        self.failed = error is not None
        self.error = error
        if error is not None and self.current_step_key is not None:
            step = self._by_key.get(self.current_step_key)
            if step is not None and step.state is ItemState.RUNNING:
                self._fail_step(step, error, at)

    def reset_for_retry(self) -> None:
        """Clear the finished run before its work is started again; the
        retry's `RunStarted` rebuilds the steps (completed stages come back
        as already complete). The wall clock keeps running."""
        self.version += 1
        self.finished = False
        self.failed = False
        self.error = None
        self.finished_at = None
        self.run_outcome = None
        self.current_step_key = None

    # -- helpers -------------------------------------------------------------

    def _start_run(self, project: str, plan: tuple[PlanEntry, ...]) -> None:
        self.project = project
        self.steps = [
            StepView(
                key=entry.key,
                label=entry.label,
                kind=entry.kind,
                params=dict(entry.params),
                weight=entry.weight,
                enabled=entry.enabled,
                state=ItemState.PENDING if entry.enabled else ItemState.DISABLED,
            )
            for entry in plan
        ]
        self._by_key = {step.key: step for step in self.steps}
        self.sessions = {}
        self._bar_owner = {}
        self.run_outcome = None
        self.current_step_key = None

    def _finish_run(self, outcome: RunOutcome, error: str | None, at: float) -> None:
        self.run_outcome = outcome
        if outcome is RunOutcome.FAILED and self.current_step_key is not None:
            step = self._by_key.get(self.current_step_key)
            if step is not None and step.state is ItemState.RUNNING:
                self._fail_step(step, error or "run failed", at)
        self.current_step_key = None

    def _ensure_step(self, key: str, kind: PlanKind) -> StepView:
        # Steps outside the plan (none expected) still get a row.
        step = self._by_key.get(key)
        if step is None:
            step = StepView(
                key=key, label=key, kind=kind, params={}, weight=1, enabled=True
            )
            self.steps.append(step)
            self._by_key[key] = step
        return step

    def _leave(self, key: str) -> None:
        if self.current_step_key == key:
            self.current_step_key = None

    @staticmethod
    def _fail_step(step: StepView, error: str, at: float) -> None:
        if step.state is ItemState.RUNNING and step.started_at is not None:
            step.elapsed = at - step.started_at
        step.state = ItemState.FAILED
        step.error = error

    def _progress(
        self,
        event: ProgressStarted | ProgressAdvanced | ProgressFinished,
        stage: str | None,
    ) -> None:
        if isinstance(event, ProgressStarted):
            owner = stage if stage in self._by_key else self.current_step_key
            if owner is None:
                return
            self._by_key[owner].bars[event.scope] = ProgressView(
                event.scope, event.label, event.total
            )
            self._bar_owner[event.scope] = owner
            return
        owner = self._bar_owner.get(event.scope)
        step = self._by_key.get(owner) if owner is not None else None
        bar = step.bars.get(event.scope) if step is not None else None
        if bar is None:
            return
        if isinstance(event, ProgressAdvanced):
            bar.completed += event.n
            if event.note is not None:
                bar.note = event.note
        else:
            if bar.total is not None:
                bar.completed = bar.total
            bar.done = True

    def _session_started(
        self, event: AgentSessionStarted, at: float, stage: str | None
    ) -> None:
        owner = event.stage or stage
        session = self.sessions.get(event.task)
        if session is None:
            self.sessions[event.task] = SessionView(
                task=event.task,
                stage=owner,
                backend=event.backend,
                model=event.model,
                effort=event.effort,
                started_at=at,
                span=chunk_range(event.task),
            )
            return
        # The next attempt of a task whose session failed.
        session.attempts += 1
        session.state = SessionState.RUNNING
        session.outcome = None
        session.started_at = at
        session.stage = owner
        session.backend = event.backend
        session.model = event.model
        session.effort = event.effort

    def _activity(self, event: AgentActivity) -> None:
        entry = ActivityEntry(event.task, event.kind, event.summary)
        self.activity.append(entry)
        session = self.sessions.get(event.task)
        if session is None:
            return
        session.activity.append(entry)
        if event.kind is ActivityKind.TOOL_CALL:
            session.tool_calls += 1
        elif event.kind is ActivityKind.REPAIR:
            session.repairs += 1

    def _session_finished(self, event: AgentSessionFinished) -> None:
        session = self.sessions.get(event.task)
        if session is None:
            return
        session.state = (
            SessionState.OK
            if event.outcome is SessionOutcome.OK
            else SessionState.FAILED
        )
        session.outcome = event.outcome
        session.elapsed = event.elapsed
        # The event's count is authoritative over the live REPAIR tally.
        session.repairs = session.repairs_before + event.repairs
        session.repairs_before = session.repairs
        session.usage.update(event.usage)

    def _log(self, event: LogLine) -> None:
        entry = LogEntry(event.level, event.text)
        step = self._by_key.get(event.stage) if event.stage else None
        (step.log if step is not None else self.pipeline_log).append(entry)
        session = self.sessions.get(event.task) if event.task else None
        if session is not None:
            session.log.append(entry)


_SKIP_STATE = {
    SkipReason.ALREADY_COMPLETE: ItemState.CACHED,
    SkipReason.DISABLED: ItemState.DISABLED,
    SkipReason.BREAKPOINT: ItemState.SKIPPED,
}
