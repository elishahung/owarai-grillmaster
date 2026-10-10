from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import pytest
from tests.pipeline.fakes import Journal, fake_side_task

from grillmaster.core.stage_key import SideTaskKey, StageKey
from grillmaster.pipeline.side_tasks import SideTaskManager
from grillmaster.pipeline.stage import StageContext, StateStore
from grillmaster.pipeline.steps import UsageCollector

if TYPE_CHECKING:
    from grillmaster.agents.runner import AgentRunner
    from grillmaster.config.load import LoadedConfig
    from grillmaster.events.bus import EventBus
    from grillmaster.pipeline.side_tasks import SideTaskDef
    from grillmaster.pipeline.stage import RunOptions
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState


def test_an_interrupt_does_not_wait_for_running_side_tasks(
    *,
    layout: ProjectLayout,
    state: ProjectState,
    loaded: LoadedConfig,
    options: RunOptions,
    agents: AgentRunner,
    bus: EventBus,
):
    release = threading.Event()

    def stuck(ctx: StageContext) -> None:
        release.wait(timeout=10)

    store = StateStore(layout, state)

    def context(task: SideTaskDef) -> StageContext:
        return StageContext(
            layout=layout,
            store=store,
            loaded=loaded,
            options=options,
            agents=agents,
            events=bus,
            workdir=layout.side_dir(task.key),
        )

    task = fake_side_task(SideTaskKey.COVER, StageKey.METADATA, Journal(), action=stuck)
    manager = SideTaskManager(
        (task,),
        context=context,
        options=options,
        config=loaded.config,
        events=bus,
        usage=UsageCollector(),
        interrupt_wait_s=0.05,
    )

    def interrupted_stage_loop() -> None:
        with manager:
            manager.stage_passed(StageKey.METADATA)
            raise KeyboardInterrupt

    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        interrupted_stage_loop()
    assert time.monotonic() - started < 5
    release.set()
