"""The thread hand-off between the emitting threads and the dashboard.

`TuiSink.emit` runs on whichever thread emitted (pipeline, side task, agent
worker); it stamps the event with the time and the step/task scope of that
thread and queues it. The Textual thread drains the queue into its
`PipelineState` before each render (`feed`), so the state never needs a
lock and an activity burst never blocks an agent thread on the UI. Once the
dashboard is gone nothing drains the queue, so `detach` turns the sink into
a pass-through (or a drop) for whatever the work still emits.

`log_bridge` turns loguru records into `LogLine` events for the sink.
"""

from __future__ import annotations

import queue
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from grillmaster.events.context import current_stage, current_task
from grillmaster.events.types import LogLine

if TYPE_CHECKING:
    from collections.abc import Callable

    from loguru import Message

    from grillmaster.events.bus import EventSink
    from grillmaster.events.types import Event
    from grillmaster.tui.state import PipelineState

LOG_FORMAT = "{time:HH:mm:ss} {level.name[0]} {message}"


@dataclass(frozen=True, slots=True)
class Observed:
    """An event as the sink saw it: when, and in which step/task scope."""

    event: Event
    at: float
    stage: str | None
    task: str | None


@dataclass(frozen=True, slots=True)
class WorkDone:
    """The dashboard's work returned (`error=None`) or raised."""

    error: str | None
    at: float


class TuiSink:
    """An `EventSink` queueing events for the dashboard; thread-safe.

    `clock` must match the `PipelineState` clock.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._queue: queue.SimpleQueue[Observed | WorkDone] = queue.SimpleQueue()
        self._detached = False
        self._forward: EventSink | None = None

    def emit(self, event: Event) -> None:
        if self._detached:
            if self._forward is not None:
                self._forward.emit(event)
            return
        self._queue.put(Observed(event, self._clock(), current_stage(), current_task()))

    def work_done(self, error: str | None) -> None:
        if not self._detached:
            self._queue.put(WorkDone(error, self._clock()))

    def detach(self, forward: EventSink | None = None) -> None:
        """Stop queueing: later events go to `forward` (or are dropped) and
        whatever is queued is discarded. For when no dashboard drains it."""
        self._forward = forward
        self._detached = True
        self.drain()

    def drain(self) -> list[Observed | WorkDone]:
        """Everything queued so far, oldest first."""
        items: list[Observed | WorkDone] = []
        while True:
            try:
                items.append(self._queue.get_nowait())
            except queue.Empty:
                return items

    def feed(self, state: PipelineState) -> int:
        """Apply everything queued to `state`; returns how many items."""
        items = self.drain()
        for item in items:
            if isinstance(item, WorkDone):
                state.work_finished(item.error, at=item.at)
            else:
                state.apply(item.event, at=item.at, stage=item.stage)
        return len(items)


def log_bridge(sink: EventSink) -> Callable[[Message], None]:
    """A loguru sink function emitting each record as a `LogLine` (text
    formatted with `LOG_FORMAT` plus any traceback) into `sink`.

    Add it with `enqueue=False`: it then runs on the logging thread, whose
    step/task scope the line is attributed to.
    """

    def write(message: Message) -> None:
        record = message.record
        sink.emit(
            LogLine(
                level=record["level"].name,
                text=str(message).rstrip("\n"),
                stage=current_stage(),
                task=current_task(),
            )
        )

    return write
