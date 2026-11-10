"""A failed stage's retry inside the same run, requested by the dashboard.

A run whose stage fails waits for its side tasks before it ends (their cost
is already paid). While it waits, the runner offers the failed stage again
through this gate: the dashboard's `request` re-runs the stage in place,
beside the side tasks still running, instead of after them.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

# How often a waiting offer checks whether the side tasks are done.
POLL_S = 0.2


class StageRetry:
    """One offer at a time: the pipeline thread waits in `offer`, the
    dashboard's thread calls `request`."""

    def __init__(self, poll_s: float = POLL_S) -> None:
        self._requested = threading.Event()
        self._poll_s = poll_s

    def request(self) -> None:
        """Retry the stage on offer."""
        self._requested.set()

    def offer(self, announce: Callable[[], None], *, over: Callable[[], bool]) -> bool:
        """Drop any earlier request, `announce` the offer, then wait until a
        retry is requested (`True`) or `over()` holds (`False`); a request
        racing the end still counts."""
        self._requested.clear()
        announce()
        while not self._requested.wait(self._poll_s) and not over():
            pass
        requested = self._requested.is_set()
        self._requested.clear()
        return requested
