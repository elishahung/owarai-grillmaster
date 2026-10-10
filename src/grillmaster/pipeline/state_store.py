"""The run's `ProjectState` and its atomic, thread-safe persistence."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from grillmaster.project.store import save_state

if TYPE_CHECKING:
    from collections.abc import Callable

    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState


class StateStore:
    """The `StateAccess` every step context of one run shares.

    Side tasks run beside the stages and write the same `project.json`, so
    every write goes through one lock. Mutate dict- or list-valued fields
    through `update` while side tasks may be running; plain field
    assignments followed by `save` are fine.
    """

    def __init__(self, layout: ProjectLayout, state: ProjectState) -> None:
        self._lock = threading.RLock()
        self._layout = layout
        self._state = state

    @property
    def layout(self) -> ProjectLayout:
        return self._layout

    @property
    def state(self) -> ProjectState:
        return self._state

    def relocate(self, layout: ProjectLayout) -> None:
        """Save into `layout` from now on: the project directory moved
        (archive), with the state already on disk there."""
        with self._lock:
            self._layout = layout

    def save(self) -> None:
        with self._lock:
            save_state(self._layout, self._state)

    def update(self, change: Callable[[ProjectState], None]) -> None:
        """Apply `change` and save, both under the lock."""
        with self._lock:
            change(self._state)
            save_state(self._layout, self._state)
