"""Side tasks: optional agent work that runs beside the stages.

A side task starts on a worker thread once the pipeline has passed its
`start_after` stage (ran it, found it already complete, or found it
disabled for this run). It never fails the run: a failure is reported and
logged. A later stage that reads its output waits for it
(`StageContext.side_tasks`). Every started task is joined when the stages
end, including when a stage failed, because its cost is already paid. A
`--break-after` run skips side tasks entirely.
"""

from __future__ import annotations

import contextlib
import contextvars
import threading
import time
from typing import TYPE_CHECKING, Any

from loguru import logger

from grillmaster.events.types import PlanKind, SkipReason, StepSkipped
from grillmaster.pipeline.steps import UsageCollector, execute_step

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from types import TracebackType

    from grillmaster.config.model import AppConfig
    from grillmaster.core.stage_key import SideTaskKey, StageKey
    from grillmaster.events.bus import EventSink
    from grillmaster.stages.base import (
        RunOptions,
        SideTaskDef,
        StageContext,
        StepOutcome,
    )

# How long an interrupted run (Ctrl-C) waits for running side tasks before it
# leaves them to die with the process (their threads are daemons).
INTERRUPT_WAIT_S = 2.0
# A wait polls: a thread join is not interruptible on Windows.
_WAIT_POLL_S = 0.5


class SideTaskManager:
    """Starts side tasks as the pipeline passes their stage; joins them on exit.

    Use as a context manager around the stage loop so the join happens
    whatever the loop raises. An interrupt (`KeyboardInterrupt` and other
    non-`Exception` errors) waits only briefly. A finished task's record
    (elapsed time by the injected clock, agent usage) is written here.
    """

    def __init__(
        self,
        tasks: Sequence[SideTaskDef[Any]],
        *,
        context: Callable[[SideTaskDef[Any]], StageContext],
        options: RunOptions,
        config: AppConfig,
        events: EventSink,
        usage: UsageCollector,
        clock: Callable[[], float] = time.monotonic,
        interrupt_wait_s: float = INTERRUPT_WAIT_S,
    ) -> None:
        self._tasks = tuple(tasks)
        self._context = context
        self._options = options
        self._config = config
        self._events = events
        self._usage = usage
        self._clock = clock
        self._interrupt_wait_s = interrupt_wait_s
        self._threads: dict[SideTaskKey, threading.Thread] = {}

    def __enter__(self) -> SideTaskManager:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        interrupted = exc_type is not None and not issubclass(exc_type, Exception)
        self.join(timeout=self._interrupt_wait_s if interrupted else None)

    def stage_passed(self, key: StageKey) -> None:
        """The pipeline is past `key`: start (or skip) the tasks waiting on it."""
        for task in self._tasks:
            if task.start_after == key:
                self._dispatch(task)

    def wait(self, key: SideTaskKey) -> None:
        """Block until task `key` has finished, if this run started it."""
        thread = self._threads.get(key)
        if thread is None or not thread.is_alive():
            return
        logger.info(f"Waiting for the {key} side task")
        while thread.is_alive():
            thread.join(_WAIT_POLL_S)

    def join(self, timeout: float | None = None) -> None:
        """Wait for the started tasks; with `timeout`, at most that long in all."""
        deadline = None if timeout is None else time.monotonic() + timeout
        for thread in self._threads.values():
            remaining = None if deadline is None else deadline - time.monotonic()
            thread.join(None if remaining is None else max(0.0, remaining))
            if thread.is_alive():
                logger.warning(f"{thread.name} is still running; not waiting for it")
        self._threads.clear()

    def _dispatch(self, task: SideTaskDef[Any]) -> None:
        ctx = self._context(task)
        if not self._options.complete_run:
            self._skip(task, ctx, SkipReason.BREAKPOINT)
            return
        if not task.enabled(self._options, self._config) or (
            task.applies is not None and not task.applies(ctx)
        ):
            self._skip(task, ctx, SkipReason.DISABLED)
            return
        if task.done(ctx.state):
            self._skip(task, ctx, SkipReason.ALREADY_COMPLETE)
            return
        # A fresh copy per task: one Context cannot be entered by two threads.
        context = contextvars.copy_context()
        thread = threading.Thread(
            target=context.run,
            args=(self._work, task, ctx),
            name=f"side-{task.key}",
            daemon=True,
        )
        self._threads[task.key] = thread
        thread.start()

    def _skip(
        self, task: SideTaskDef[Any], ctx: StageContext, reason: SkipReason
    ) -> None:
        if task.on_skip is not None:
            task.on_skip(ctx, reason)
        self._events.emit(StepSkipped(task.key, PlanKind.SIDE_TASK, reason))

    def _work[T](self, task: SideTaskDef[T], ctx: StageContext) -> None:
        def finish(outcome: StepOutcome[T]) -> str | None:
            ctx.update(lambda state: task.store(state, outcome))
            return task.describe(outcome.value)

        # Already logged and reported by `execute_step`; never fails the run.
        with contextlib.suppress(Exception):
            execute_step(
                task.key,
                PlanKind.SIDE_TASK,
                lambda: task.run(ctx),
                events=self._events,
                clock=self._clock,
                usage=self._usage,
                finish=finish,
            )
