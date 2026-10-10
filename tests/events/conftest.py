from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Iterator

    from loguru import Message, Record


@pytest.fixture
def log_records() -> Iterator[list[Record]]:
    """Every loguru record emitted during the test, DEBUG and up."""
    records: list[Record] = []

    def sink(message: Message) -> None:
        records.append(message.record)

    handler = logger.add(sink, level="DEBUG")
    yield records
    logger.remove(handler)
