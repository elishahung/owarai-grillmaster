from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest
from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Iterator

    from loguru import Message, Record

    from grillmaster.events.types import Event


class RecordingSink:
    """Collects every emitted event, in order; thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[Event] = []

    def emit(self, event: Event) -> None:
        with self._lock:
            self._events.append(event)

    @property
    def events(self) -> list[Event]:
        with self._lock:
            return list(self._events)


@pytest.fixture
def recording_sink() -> RecordingSink:
    return RecordingSink()


@pytest.fixture
def log_records() -> Iterator[list[Record]]:
    """Every loguru record emitted during the test, DEBUG and up."""
    records: list[Record] = []

    def sink(message: Message) -> None:
        records.append(message.record)

    handler = logger.add(sink, level="DEBUG")
    yield records
    logger.remove(handler)
