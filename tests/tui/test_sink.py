from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import pytest
from loguru import logger
from tests.tui.fakes import PLAN, FakeClock

from grillmaster.events.context import stage_scope, task_scope
from grillmaster.events.types import (
    ActivityKind,
    AgentActivity,
    AgentSessionStarted,
    LogLine,
    PlanKind,
    ProgressStarted,
    RunStarted,
    StepStarted,
)
from grillmaster.tui.sink import Observed, TuiSink, WorkDone, log_bridge
from grillmaster.tui.state import LogEntry, PipelineState

if TYPE_CHECKING:
    from collections.abc import Iterator

    from grillmaster.events.types import Event


def test_emit_stamps_time_and_the_emitting_scope():
    clock = FakeClock(42.0)
    sink = TuiSink(clock)
    event = ProgressStarted("dl", "Downloading", 1.0)

    def emit_in_side_task() -> None:
        with stage_scope("cover"), task_scope("cover"):
            sink.emit(event)

    thread = threading.Thread(target=emit_in_side_task)
    thread.start()
    thread.join()

    assert sink.drain() == [Observed(event, 42.0, "cover", "cover")]
    assert sink.drain() == []


def test_feed_applies_events_in_order_and_attributes_by_scope():
    clock = FakeClock()
    sink = TuiSink(clock)
    state = PipelineState(clock)
    sink.emit(RunStarted("p", PLAN))
    with stage_scope("download"):
        sink.emit(StepStarted("download", PlanKind.STAGE))
        sink.emit(ProgressStarted("dl", "Downloading", 1.0))
    sink.work_done("boom")

    assert sink.feed(state) == 4
    download = state.step("download")
    assert download is not None
    assert "dl" in download.bars
    assert state.finished
    assert state.error == "boom"
    assert sink.feed(state) == 0


def test_concurrent_emitters_lose_nothing():
    sink = TuiSink()
    state = PipelineState()
    workers, per_worker = 8, 250

    def burst(worker: int) -> None:
        task = f"chunks/{worker:04d}-{worker:04d}"
        with stage_scope("chunks"), task_scope(task):
            sink.emit(AgentSessionStarted(task, "chunks", "agy", "m", None))
            for n in range(per_worker):
                sink.emit(AgentActivity(task, ActivityKind.TOOL_CALL, f"call {n}"))

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(burst, range(workers)))
    sink.feed(state)

    assert len(state.sessions) == workers
    assert all(s.tool_calls == per_worker for s in state.sessions.values())


def test_work_done_is_queued_after_earlier_events():
    sink = TuiSink(FakeClock(7.0))
    sink.emit(RunStarted("p", PLAN))
    sink.work_done(None)
    items = sink.drain()
    assert isinstance(items[0], Observed)
    assert items[1] == WorkDone(None, 7.0)


@pytest.fixture
def bridged() -> Iterator[TuiSink]:
    sink = TuiSink()
    handler = logger.add(
        log_bridge(sink), level="DEBUG", format="{level.name[0]} {message}"
    )
    yield sink
    logger.remove(handler)


def test_log_bridge_attributes_records_to_the_logging_thread_scope(bridged):
    sink = bridged

    def side_task_logs() -> None:
        with stage_scope("date_research"), task_scope("date_research"):
            logger.warning("searching")

    logger.info("outside")
    thread = threading.Thread(target=side_task_logs)
    thread.start()
    thread.join()

    lines = [item.event for item in sink.drain() if isinstance(item, Observed)]
    assert lines == [
        LogLine("INFO", "I outside"),
        LogLine("WARNING", "W searching", "date_research", "date_research"),
    ]


def test_log_bridge_lines_land_in_the_step_log(bridged):
    sink = bridged
    state = PipelineState()
    state.apply(RunStarted("p", PLAN))
    with stage_scope("download"):
        logger.success("downloaded")
    sink.feed(state)
    download = state.step("download")
    assert download is not None
    assert list(download.log) == [LogEntry("SUCCESS", "S downloaded")]


class _Recorder:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def emit(self, event: Event) -> None:
        self.events.append(event)


def test_detach_discards_the_queue_and_forwards_later_events():
    sink = TuiSink()
    sink.emit(LogLine("INFO", "queued"))
    forward = _Recorder()
    sink.detach(forward)
    late = LogLine("INFO", "late")
    sink.emit(late)
    sink.work_done(None)
    assert sink.drain() == []
    assert forward.events == [late]


def test_detach_without_a_target_drops_events():
    sink = TuiSink()
    sink.detach()
    sink.emit(LogLine("INFO", "dropped"))
    assert sink.drain() == []
