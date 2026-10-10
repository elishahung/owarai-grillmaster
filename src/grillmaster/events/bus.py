"""Synchronous, thread-safe fan-out of events to sinks."""

from __future__ import annotations

import threading
from contextvars import ContextVar
from typing import TYPE_CHECKING, Protocol

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Iterable

    from grillmaster.events.types import Event


# Set while a sink failure is being logged: a loguru-to-bus bridge would turn
# that log line into another event, and a sink failing on every event would
# then recurse without end.
_reporting_failure: ContextVar[bool] = ContextVar(
    "grill_reporting_failure", default=False
)


class EventSink(Protocol):
    def emit(self, event: Event) -> None: ...


class EventBus:
    """Delivers each event to every subscribed sink, in subscription order.

    Delivery runs on the emitting thread, so sinks see the emitter's
    contextvars and must be thread-safe themselves. A failing sink is logged
    and skipped; it never stops delivery to the others or reaches the
    emitter. The bus is itself an `EventSink`.
    """

    def __init__(self, sinks: Iterable[EventSink] = ()) -> None:
        self._lock = threading.Lock()
        self._sinks: tuple[EventSink, ...] = tuple(sinks)

    def subscribe(self, sink: EventSink) -> None:
        with self._lock:
            self._sinks = (*self._sinks, sink)

    def unsubscribe(self, sink: EventSink) -> None:
        """Remove `sink`; raises `ValueError` when it is not subscribed."""
        with self._lock:
            sinks = list(self._sinks)
            sinks.remove(sink)
            self._sinks = tuple(sinks)

    def emit(self, event: Event) -> None:
        # Copy-on-write tuple: sinks run outside the lock and may subscribe,
        # unsubscribe or emit re-entrantly.
        for sink in self._sinks:
            try:
                sink.emit(event)
            except Exception:  # noqa: BLE001 - one broken sink must not stop the run
                if _reporting_failure.get():
                    continue
                token = _reporting_failure.set(True)
                try:
                    logger.opt(exception=True).error(
                        f"Event sink {type(sink).__name__} failed on "
                        f"{type(event).__name__}"
                    )
                finally:
                    _reporting_failure.reset(token)
