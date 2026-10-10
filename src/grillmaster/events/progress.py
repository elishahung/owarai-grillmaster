"""Progress bars over an event sink, and the bar scopes sinks look for."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from grillmaster.events.types import ProgressAdvanced, ProgressFinished, ProgressStarted

if TYPE_CHECKING:
    from collections.abc import Iterator

    from grillmaster.events.bus import EventSink

# The chunk stage's bar: one step per chunk whose translation is in hand
# (cache hits count at once). The TUI's chunk board reads it.
CHUNK_PROGRESS_SCOPE = "chunks"


class Advance:
    """Moves one open bar forward; safe to call from any thread."""

    def __init__(self, sink: EventSink, scope: str) -> None:
        self._sink = sink
        self.scope = scope

    def __call__(self, n: float = 1.0, note: str | None = None) -> None:
        self._sink.emit(ProgressAdvanced(self.scope, n, note))


@contextmanager
def track(
    sink: EventSink, scope: str, label: str, total: float | None
) -> Iterator[Advance]:
    """A bar `scope` of `total` steps (`None`: indeterminate) for the body.

    The bar finishes only when the body returns: one that raises leaves it
    open where it stands, and the failing step's `StepFailed` ends it, so a
    failed bar never reads as complete.
    """
    sink.emit(ProgressStarted(scope, label, total))
    yield Advance(sink, scope)
    sink.emit(ProgressFinished(scope))
