from __future__ import annotations

import threading
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest
from tests.pipeline.fakes import Journal, failing, fake_side_task, ticking_clock

from grillmaster.core.stage_key import SideTaskKey, StageKey
from grillmaster.events.types import (
    AgentSessionFinished,
    PlanKind,
    SessionOutcome,
    StepCompleted,
)
from grillmaster.pipeline.side_tasks import SideTaskManager
from grillmaster.pipeline.state_store import StateStore
from grillmaster.pipeline.steps import UsageCollector
from grillmaster.project.state import DateResearchRecord, now
from grillmaster.project.store import load_state
from grillmaster.stages.base import StageContext

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.fakes import RecordingSink

    from grillmaster.agents.runner import AgentRunner
    from grillmaster.config.load import LoadedConfig
    from grillmaster.events.bus import EventBus
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState
    from grillmaster.stages.base import Externals, RunOptions, SideTaskDef, StepOutcome


@pytest.fixture
def make_manager(
    *,
    layout: ProjectLayout,
    state: ProjectState,
    loaded: LoadedConfig,
    options: RunOptions,
    agents: AgentRunner,
    bus: EventBus,
    externals: Externals,
) -> Callable[..., SideTaskManager]:
    store = StateStore(layout, state)

    def context(task: SideTaskDef[Any]) -> StageContext:
        return StageContext(
            layout=layout,
            store=store,
            loaded=loaded,
            options=options,
            agents=agents,
            events=bus,
            externals=externals,
            workdir=layout.side_dir(task.key),
        )

    def make(*tasks: SideTaskDef[Any], **kwargs: Any) -> SideTaskManager:
        return SideTaskManager(
            tasks,
            context=context,
            options=options,
            config=loaded.config,
            events=bus,
            **{"usage": UsageCollector(), **kwargs},
        )

    return make


def session(usage: UsageCollector) -> Callable[[StageContext], str]:
    """A task action finishing one agent session, as the bus would report it."""

    def action(ctx: StageContext) -> str:
        usage.emit(AgentSessionFinished("t", SessionOutcome.OK, 1.0, 0, {"in": 7}))
        return "drawn"

    return action


def test_the_manager_records_elapsed_and_usage(
    make_manager: Callable[..., SideTaskManager],
    layout: ProjectLayout,
    recording_sink: RecordingSink,
):
    usage = UsageCollector()
    task = fake_side_task(
        SideTaskKey.COVER, StageKey.METADATA, Journal(), action=session(usage)
    )

    with make_manager(task, usage=usage, clock=ticking_clock(2.0)) as manager:
        manager.stage_passed(StageKey.METADATA)

    record = load_state(layout).side_tasks.cover
    assert record is not None
    assert (record.elapsed_s, record.agent_usage) == (2.0, {"in": 7})
    assert (
        StepCompleted(SideTaskKey.COVER, PlanKind.SIDE_TASK, 2.0, "drawn")
        in recording_sink.events
    )


def test_a_task_record_replaces_the_default(
    make_manager: Callable[..., SideTaskManager],
    layout: ProjectLayout,
    recording_sink: RecordingSink,
):
    def record(state: ProjectState, outcome: StepOutcome[str | None]) -> None:
        state.side_tasks.date_research = DateResearchRecord(
            completed_at=now(), elapsed_s=outcome.elapsed, verdict="unknown"
        )

    task = replace(
        fake_side_task(
            SideTaskKey.DATE_RESEARCH,
            StageKey.METADATA,
            Journal(),
            action=lambda _ctx: "payload",
        ),
        record=record,
        describe=lambda value: f"<{value}>",
    )

    with make_manager(task, clock=ticking_clock()) as manager:
        manager.stage_passed(StageKey.METADATA)

    saved = load_state(layout).side_tasks
    assert saved.date_research is not None
    assert saved.date_research.verdict == "unknown"
    assert saved.cover is None
    assert (
        StepCompleted(SideTaskKey.DATE_RESEARCH, PlanKind.SIDE_TASK, 1.0, "<payload>")
        in recording_sink.events
    )


def test_a_failed_task_is_not_recorded(
    make_manager: Callable[..., SideTaskManager], layout: ProjectLayout
):
    task = fake_side_task(
        SideTaskKey.COVER, StageKey.METADATA, Journal(), action=failing("boom")
    )

    with make_manager(task) as manager:
        manager.stage_passed(StageKey.METADATA)

    assert load_state(layout).side_tasks.cover is None


def test_an_interrupt_does_not_wait_for_running_side_tasks(
    make_manager: Callable[..., SideTaskManager],
):
    release = threading.Event()

    def stuck(ctx: StageContext) -> None:
        release.wait(timeout=10)

    task = fake_side_task(SideTaskKey.COVER, StageKey.METADATA, Journal(), action=stuck)
    manager = make_manager(task, interrupt_wait_s=0.05)

    def interrupted_stage_loop() -> None:
        with manager:
            manager.stage_passed(StageKey.METADATA)
            raise KeyboardInterrupt

    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        interrupted_stage_loop()
    assert time.monotonic() - started < 5
    release.set()
