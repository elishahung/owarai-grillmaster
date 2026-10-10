from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from grillmaster.events.bus import EventBus
from grillmaster.events.types import LogLine, PlanKind, StepCompleted, StepStarted

if TYPE_CHECKING:
    from loguru import Message, Record
    from tests.fakes import RecordingSink

    from grillmaster.events.types import Event

STAGE = PlanKind.STAGE


class ExplodingSink:
    def emit(self, event: Event) -> None:
        raise RuntimeError("boom")


def test_events_fan_out_in_subscription_order(recording_sink: RecordingSink):
    order: list[str] = []

    class Tagging:
        def __init__(self, tag: str) -> None:
            self.tag = tag

        def emit(self, event: Event) -> None:
            order.append(self.tag)

    bus = EventBus([Tagging("a"), recording_sink])
    bus.subscribe(Tagging("b"))
    bus.emit(StepStarted("prepass", STAGE))
    assert order == ["a", "b"]
    assert recording_sink.events == [StepStarted("prepass", STAGE)]


def test_unsubscribe_stops_delivery(recording_sink: RecordingSink):
    bus = EventBus()
    bus.subscribe(recording_sink)
    bus.emit(StepStarted("asr", STAGE))
    bus.unsubscribe(recording_sink)
    bus.emit(StepStarted("audio", STAGE))
    assert recording_sink.events == [StepStarted("asr", STAGE)]
    with pytest.raises(ValueError, match=r"not in list|x not in"):
        bus.unsubscribe(recording_sink)


def test_failing_sink_is_logged_and_others_still_receive(
    recording_sink: RecordingSink, log_records: list[Record]
):
    bus = EventBus([ExplodingSink(), recording_sink])
    bus.emit(StepCompleted("chunks", STAGE, 1.5))
    assert recording_sink.events == [StepCompleted("chunks", STAGE, 1.5)]
    assert len(log_records) == 1
    record = log_records[0]
    assert record["level"].name == "ERROR"
    assert record["message"] == "Event sink ExplodingSink failed on StepCompleted"
    assert record["exception"] is not None
    assert str(record["exception"].value) == "boom"


def test_failure_logging_does_not_recurse_through_a_log_bridge(
    log_records: list[Record],
):
    bus = EventBus([ExplodingSink()])

    def bridge(message: Message) -> None:
        bus.emit(LogLine(message.record["level"].name, message.record["message"]))

    handler = logger.add(bridge, level="ERROR")
    try:
        bus.emit(StepStarted("asr", STAGE))
    finally:
        logger.remove(handler)
    assert [record["level"].name for record in log_records] == ["ERROR"]


def test_bus_is_a_sink(recording_sink: RecordingSink):
    inner = EventBus([recording_sink])
    EventBus([inner]).emit(StepStarted("refine", STAGE))
    assert recording_sink.events == [StepStarted("refine", STAGE)]


def test_sink_may_unsubscribe_itself_during_delivery(recording_sink: RecordingSink):
    bus = EventBus()

    class OneShot:
        def emit(self, event: Event) -> None:
            bus.unsubscribe(self)

    bus.subscribe(OneShot())
    bus.subscribe(recording_sink)
    bus.emit(StepStarted("a", STAGE))
    bus.emit(StepStarted("b", STAGE))
    assert recording_sink.events == [StepStarted("a", STAGE), StepStarted("b", STAGE)]


def test_concurrent_emits_are_all_delivered(recording_sink: RecordingSink):
    bus = EventBus([recording_sink])

    def worker(n: int) -> None:
        for i in range(200):
            bus.emit(StepStarted(f"{n}-{i}", STAGE))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(recording_sink.events) == 1600
