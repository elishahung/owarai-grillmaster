"""Run one project through the stages, side tasks, delivery and archive.

Per stage, in registry order: skip it when disabled for this run, skip it
(running `on_skip`) when the ledger already has it, otherwise run it as a
step and record it in the ledger with an atomic save. The run stops after the
`--break-after` stage whether that stage ran, was already complete or was
disabled. Side tasks start as the loop passes their stage and are joined when
the loop ends, however it ends. Then come the delivery steps, the project's
logs close, and the archive move (when one is wired) runs last.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.agents.adapters import AdapterRegistry
from grillmaster.agents.runner import AgentRunner
from grillmaster.events.bus import EventBus
from grillmaster.events.context import stage_scope
from grillmaster.events.types import (
    PlanKind,
    RunFinished,
    RunOutcome,
    RunStarted,
    SkipReason,
    StepSkipped,
)
from grillmaster.pipeline.delivery import run_archive, run_delivery
from grillmaster.pipeline.logs import ProjectLogs
from grillmaster.pipeline.projects import open_project
from grillmaster.pipeline.registry import PIPELINE
from grillmaster.pipeline.side_tasks import SideTaskManager
from grillmaster.pipeline.stage import StageContext, StateStore
from grillmaster.pipeline.steps import UsageCollector, execute_step
from grillmaster.project.state import now as local_now

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime
    from pathlib import Path

    from grillmaster.config.load import LoadedConfig
    from grillmaster.events.bus import EventSink
    from grillmaster.pipeline.delivery import Archive
    from grillmaster.pipeline.registry import Pipeline
    from grillmaster.pipeline.stage import RunOptions, StageDef
    from grillmaster.pipeline.steps import StepOutcome
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState

_SECONDS_PER_MINUTE = 60


def run_project(
    loaded: LoadedConfig,
    options: RunOptions,
    *,
    sinks: Sequence[EventSink],
    pipeline: Pipeline = PIPELINE,
) -> ProjectLayout:
    """Check `options`, open (or create) the project and run it; returns the
    project's final layout.

    Rejected options create nothing. The agent runner is built from
    `[agents]`; events go to `sinks` (plus the project's JSONL log).
    """
    pipeline.check(options)
    layout, state = open_project(loaded.projects_root, options)
    events = EventBus(sinks)
    settings = loaded.config.agents
    agents = AgentRunner(
        settings.roles.specs(),
        AdapterRegistry(),
        max_concurrent=settings.max_concurrent,
        timeout_s=settings.timeout_minutes * _SECONDS_PER_MINUTE,
        events=events,
    )
    return run_pipeline(
        layout,
        state,
        loaded=loaded,
        options=options,
        agents=agents,
        events=events,
        pipeline=pipeline,
    )


def run_pipeline(
    layout: ProjectLayout,
    state: ProjectState,
    *,
    loaded: LoadedConfig,
    options: RunOptions,
    agents: AgentRunner,
    events: EventBus,
    pipeline: Pipeline = PIPELINE,
    archive: Archive | None = None,
    clock: Callable[[], float] = time.monotonic,
    started_at: datetime | None = None,
) -> ProjectLayout:
    """Run an opened project; returns its final layout (moved by `archive`).

    `archive` moves the project directory and returns its new layout; it is
    planned and run only when given. Raises whatever a stage, delivery step
    or the archive raised, after the side tasks are joined and
    `RunFinished(failed)` is emitted.
    """
    pipeline.check(options)
    run = _Run(
        pipeline=pipeline,
        store=StateStore(layout, state),
        loaded=loaded,
        options=options,
        agents=agents,
        events=events,
        archive=archive,
        clock=clock,
    )
    return run.execute(started_at or local_now())


class _Run:
    def __init__(
        self,
        *,
        pipeline: Pipeline,
        store: StateStore,
        loaded: LoadedConfig,
        options: RunOptions,
        agents: AgentRunner,
        events: EventBus,
        archive: Archive | None,
        clock: Callable[[], float],
    ) -> None:
        self._pipeline = pipeline
        self._store = store
        self._loaded = loaded
        self._options = options
        self._agents = agents
        self._events = events
        self._archive = archive
        self._clock = clock
        self._usage = UsageCollector()

    def execute(self, started_at: datetime) -> ProjectLayout:
        state = self._store.state
        logs = ProjectLogs(self._events, self._store.layout, started_at)
        self._events.subscribe(self._usage)
        try:
            plan = self._pipeline.plan(
                self._options, self._loaded.config, archive=self._archive is not None
            )
            self._events.emit(RunStarted(state.id, plan))
            try:
                layout = self._steps(logs)
            except BaseException as error:
                self._events.emit(RunFinished(RunOutcome.FAILED, str(error)))
                raise
            self._events.emit(RunFinished(RunOutcome.COMPLETED))
            return layout
        finally:
            self._events.unsubscribe(self._usage)
            logs.close()

    def _steps(self, logs: ProjectLogs) -> ProjectLayout:
        self._stages()
        run_delivery(
            self._pipeline.delivery,
            layout=self._store.layout,
            context=self._context,
            options=self._options,
            config=self._loaded.config,
            events=self._events,
            clock=self._clock,
            usage=self._usage,
        )
        state = self._store.state
        logger.info(f"Project {state.id} total ASR cost: ${state.asr_cost_usd:.4f}")
        if self._archive is None:
            return self._store.layout
        return run_archive(
            self._archive,
            self._store.layout,
            logs=logs,
            options=self._options,
            events=self._events,
            clock=self._clock,
            usage=self._usage,
        )

    def _stages(self) -> None:
        with SideTaskManager(
            self._pipeline.side_tasks,
            context=lambda task: self._context(self._store.layout.side_dir(task.key)),
            options=self._options,
            config=self._loaded.config,
            events=self._events,
            usage=self._usage,
            clock=self._clock,
        ) as side_tasks:
            for stage in self._pipeline.stages:
                self._run_stage(stage)
                side_tasks.stage_passed(stage.key)
                if self._options.break_after == stage.key:
                    logger.warning(
                        f"Breakpoint reached after {stage.key}; stopping "
                        f"{self._store.state.id}"
                    )
                    return

    def _run_stage(self, stage: StageDef) -> None:
        key = stage.key
        if not stage.enabled(self._options):
            self._events.emit(StepSkipped(key, PlanKind.STAGE, SkipReason.DISABLED))
            return
        ctx = self._context(self._store.layout.work_dir(key))
        if self._store.state.is_done(key):
            with stage_scope(key):
                if stage.on_skip is not None:
                    stage.on_skip(ctx)
                self._events.emit(
                    StepSkipped(key, PlanKind.STAGE, SkipReason.ALREADY_COMPLETE)
                )
            return

        def record(outcome: StepOutcome[None]) -> None:
            params = stage.params(self._loaded.config)
            self._store.update(
                lambda state: state.mark_done(
                    key,
                    elapsed_s=outcome.elapsed,
                    params=params,
                    agent_usage=outcome.usage,
                )
            )

        execute_step(
            key,
            PlanKind.STAGE,
            lambda: stage.run(ctx),
            events=self._events,
            clock=self._clock,
            usage=self._usage,
            finish=record,
        )

    def _context(self, workdir: Path) -> StageContext:
        return StageContext(
            layout=self._store.layout,
            store=self._store,
            loaded=self._loaded,
            options=self._options,
            agents=self._agents,
            events=self._events,
            workdir=workdir,
        )
