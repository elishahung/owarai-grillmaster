"""Every event the pipeline emits. Events are immutable and carry no clock.

Sinks stamp the time they observe an event (`JsonlSink`), which keeps events
comparable by value in tests. Step keys are plain strings: `StageKey` values,
side tasks (`cover` / `date_research`) or delivery steps (`archive` /
`package`), told apart by `PlanKind`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


class PlanKind(StrEnum):
    STAGE = "stage"
    SIDE_TASK = "side_task"
    DELIVERY = "delivery"


class SkipReason(StrEnum):
    ALREADY_COMPLETE = "already-complete"
    DISABLED = "disabled"


class RunOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class ActivityKind(StrEnum):
    THOUGHT = "thought"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    MESSAGE = "message"
    REPAIR = "repair"


class SessionOutcome(StrEnum):
    """`ok`, or the agents-layer error category that ended the session."""

    OK = "ok"
    CONFIG_ERROR = "config_error"
    QUOTA_ERROR = "quota_error"
    AUTH_ERROR = "auth_error"
    TRANSIENT_ERROR = "transient_error"
    OUTPUT_ERROR = "output_error"


@dataclass(frozen=True, slots=True)
class PlanEntry:
    """One row of the run plan announced by `RunStarted`."""

    key: str
    label: str
    kind: PlanKind
    enabled: bool = True
    params: Mapping[str, str] = field(default_factory=dict)


# --- run lifecycle ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunStarted:
    project: str
    plan: tuple[PlanEntry, ...]


@dataclass(frozen=True, slots=True)
class RunFinished:
    outcome: RunOutcome
    error: str | None = None


@dataclass(frozen=True, slots=True)
class BatchItemStarted:
    """A serial run is about to process item `index` (1-based) of `total`."""

    index: int
    total: int
    source: str


# --- steps: stages, side tasks, delivery ------------------------------------


@dataclass(frozen=True, slots=True)
class StepStarted:
    """A side task emits this on dispatch; its session may still queue."""

    key: str
    kind: PlanKind


@dataclass(frozen=True, slots=True)
class StepCompleted:
    key: str
    kind: PlanKind
    elapsed: float
    result: str | None = None


@dataclass(frozen=True, slots=True)
class StepSkipped:
    key: str
    kind: PlanKind
    reason: SkipReason


@dataclass(frozen=True, slots=True)
class StepFailed:
    key: str
    kind: PlanKind
    error: str


# --- progress bars ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProgressStarted:
    """`scope` names the bar; `total=None` is an indeterminate bar."""

    scope: str
    label: str
    total: float | None


@dataclass(frozen=True, slots=True)
class ProgressAdvanced:
    scope: str
    n: float = 1.0
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ProgressFinished:
    scope: str


# --- agent sessions --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentSessionStarted:
    """Emitted once the session holds a concurrency slot."""

    task: str
    stage: str | None
    backend: str
    model: str
    effort: str | None


@dataclass(frozen=True, slots=True)
class AgentActivity:
    task: str
    kind: ActivityKind
    summary: str


@dataclass(frozen=True, slots=True)
class AgentSessionFinished:
    task: str
    outcome: SessionOutcome
    elapsed: float
    repairs: int
    usage: Mapping[str, int] = field(default_factory=dict)


# --- logs ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LogLine:
    level: str
    text: str
    stage: str | None = None
    task: str | None = None


# Sinks dispatch on the concrete type; `describe` matches it exhaustively.
type Event = (
    RunStarted
    | RunFinished
    | BatchItemStarted
    | StepStarted
    | StepCompleted
    | StepSkipped
    | StepFailed
    | ProgressStarted
    | ProgressAdvanced
    | ProgressFinished
    | AgentSessionStarted
    | AgentActivity
    | AgentSessionFinished
    | LogLine
)
