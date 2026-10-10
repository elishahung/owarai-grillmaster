"""The one step executor every stage, side task and delivery step runs through.

A step runs inside its `stage_scope`: `StepStarted`, the work, then
`StepCompleted` or (after logging the traceback) `StepFailed` and the error
re-raised. Agent token usage is summed per step scope and taken when the step
ends, so nothing accumulates for steps that do not record it.
"""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.events.context import current_stage, stage_scope
from grillmaster.events.types import (
    AgentSessionFinished,
    PlanKind,
    StepCompleted,
    StepFailed,
    StepStarted,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from grillmaster.events.bus import EventSink
    from grillmaster.events.types import Event


class UsageCollector:
    """Sums agent token usage per step scope, from session-finished events.

    Sinks run on the emitting thread, so `current_stage()` is the scope of
    the session that just ended (agent fan-out copies the context).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._usage: dict[str, Counter[str]] = {}

    def emit(self, event: Event) -> None:
        if not isinstance(event, AgentSessionFinished):
            return
        scope = current_stage()
        if scope is None:
            return
        with self._lock:
            self._usage.setdefault(scope, Counter()).update(event.usage)

    def take(self, scope: str) -> Mapping[str, int] | None:
        """The summed usage of `scope` (forgetting it); `None` without sessions."""
        with self._lock:
            usage = self._usage.pop(scope, None)
        return dict(usage) if usage is not None else None


@dataclass(frozen=True, slots=True)
class StepOutcome[T]:
    value: T
    elapsed: float
    usage: Mapping[str, int] | None


def _no_summary(_outcome: StepOutcome[object]) -> str | None:
    return None


def execute_step[T](
    key: str,
    kind: PlanKind,
    run: Callable[[], T],
    *,
    events: EventSink,
    clock: Callable[[], float],
    usage: UsageCollector,
    finish: Callable[[StepOutcome[T]], str | None] = _no_summary,
) -> StepOutcome[T]:
    """Run one step in `stage_scope(key)` and report it.

    `finish` runs after `run`, still inside the failure handling (a stage
    records its ledger entry there), and returns the `StepCompleted` result
    text. A failure is logged with its traceback (at DEBUG; the one-line
    message at WARNING for a side task, which never fails the run, else
    ERROR), reported as `StepFailed` and re-raised.
    """
    with stage_scope(key):
        events.emit(StepStarted(key, kind))
        started = clock()
        try:
            value = run()
            outcome = StepOutcome(value, clock() - started, usage.take(key))
            summary = finish(outcome)
        except Exception as error:
            usage.take(key)
            level = "WARNING" if kind is PlanKind.SIDE_TASK else "ERROR"
            logger.opt(exception=True).debug(f"{kind} {key} traceback")
            logger.log(level, f"{kind} {key} failed: {error}")
            events.emit(StepFailed(key, kind, str(error)))
            raise
        events.emit(StepCompleted(key, kind, outcome.elapsed, summary))
        return outcome
