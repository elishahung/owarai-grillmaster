"""In-memory pipeline state shared between the TUI reporter and the app.

This module deliberately has no Textual imports: the reporter (running on the
pipeline worker thread) mutates a lock-guarded ``PipelineState``, and the
Textual app re-renders from it on a timer. All mutation helpers take the lock;
the app takes the same lock while rendering a frame.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from time import monotonic
from typing import Any

from rich.progress import TaskID

from services.progress import PlannedStage

LOG_LINES_PER_ITEM = 2000

# Relative weights for the header's overall progress bar, keyed by plan key.
# Unknown keys default to 1.
STAGE_WEIGHTS = {
    "metadata": 1,
    "download": 4,
    "combine": 2,
    "audio": 1,
    "asr": 3,
    "srt": 1,
    "prepass": 4,
    "chunks": 12,
    "refine": 3,
    "glossary": 2,
    "finalize": 1,
    "archive": 1,
    "package": 4,
}


class ItemState(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    CACHED = "cached"  # skipped because a previous run completed it
    DISABLED = "disabled"  # optional stage/side task turned off for this run
    FAILED = "failed"


@dataclass
class SubTaskBar:
    """One live start_stage/advance/finish bar attributed to an item."""

    label: str
    total: float
    completed: float = 0.0
    description: str | None = None
    status: str = ""
    done: bool = False

    @property
    def fraction(self) -> float:
        if self.total <= 0:
            return 0.0
        return min(1.0, self.completed / self.total)


@dataclass
class ItemView:
    """One sidebar entry: a pipeline stage or a side task."""

    key: str
    label: str
    params: dict[str, str]
    kind: str  # "stage" | "side_task"
    enabled: bool
    state: ItemState = ItemState.PENDING
    started_at: float | None = None  # monotonic
    elapsed: float = 0.0  # final elapsed once finished
    result: str | None = None
    error: str | None = None
    bars: dict[TaskID, SubTaskBar] = field(default_factory=dict)
    log: deque[tuple[str, str]] = field(
        default_factory=lambda: deque(maxlen=LOG_LINES_PER_ITEM)
    )

    def live_elapsed(self) -> float:
        if self.state is ItemState.RUNNING and self.started_at is not None:
            return monotonic() - self.started_at
        return self.elapsed


class ChunkState(Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"


@dataclass
class ChunkCell:
    index: int
    from_index: int = 0
    to_index: int = 0
    state: ChunkState = ChunkState.PENDING
    retries: int = 0
    cost: float = 0.0
    started_at: float | None = None
    elapsed: float = 0.0

    def live_elapsed(self) -> float:
        if self.state is ChunkState.ACTIVE and self.started_at is not None:
            return monotonic() - self.started_at
        return self.elapsed


@dataclass
class ChunkBoard:
    """Live view of the concurrent chunk-translation stage."""

    total: int = 0
    cells: dict[int, ChunkCell] = field(default_factory=dict)
    retries: int = 0
    cost: float = 0.0

    @property
    def done(self) -> int:
        return sum(
            1 for c in self.cells.values() if c.state is ChunkState.DONE
        )

    @property
    def failed(self) -> int:
        return sum(
            1 for c in self.cells.values() if c.state is ChunkState.FAILED
        )

    @property
    def active(self) -> list[ChunkCell]:
        return [
            c for c in self.cells.values() if c.state is ChunkState.ACTIVE
        ]


class PipelineState:
    """Thread-safe model of one pipeline run for the dashboard."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.project: Any = None
        self.items: list[ItemView] = []
        self._by_key: dict[str, ItemView] = {}
        # Lines logged before any stage runs (project load etc.).
        self.pipeline_log: deque[tuple[str, str]] = deque(
            maxlen=LOG_LINES_PER_ITEM
        )
        self.chunks = ChunkBoard()
        self.started_at = monotonic()
        self.finished_at: float | None = None
        self.finished = False
        self.failed = False
        self.error: str | None = None
        self.current_stage_key: str | None = None
        self._next_task_id = 1
        self._bar_owner: dict[TaskID, str] = {}

    # ---------- accessors ----------

    def get(self, key: str) -> ItemView | None:
        return self._by_key.get(key)

    def wall_elapsed(self) -> float:
        end = self.finished_at if self.finished_at is not None else monotonic()
        return end - self.started_at

    def total_cost(self) -> float:
        return float(getattr(self.project, "total_cost", 0.0) or 0.0)

    def total_progress(self) -> float:
        """Weighted overall progress across enabled pipeline stages."""
        with self.lock:
            total = 0.0
            got = 0.0
            for item in self.items:
                if item.kind != "stage" or item.state is ItemState.DISABLED:
                    continue
                weight = STAGE_WEIGHTS.get(item.key, 1)
                total += weight
                got += weight * self._item_progress(item)
            return got / total if total > 0 else 0.0

    def _item_progress(self, item: ItemView) -> float:
        if item.state in (ItemState.DONE, ItemState.CACHED):
            return 1.0
        if item.state is not ItemState.RUNNING:
            return 0.0
        if item.key == "chunks" and self.chunks.total > 0:
            return self.chunks.done / self.chunks.total
        active = [bar for bar in item.bars.values() if not bar.done]
        if active:
            return max(bar.fraction for bar in active)
        return 0.0

    # ---------- lifecycle mutations ----------

    def on_pipeline_started(
        self, project: Any, plan: list[PlannedStage]
    ) -> None:
        with self.lock:
            self.project = project
            self.items = [
                ItemView(
                    key=entry.key,
                    label=entry.label,
                    params=dict(entry.params),
                    kind=entry.kind,
                    enabled=entry.enabled,
                    state=(
                        ItemState.PENDING
                        if entry.enabled
                        else ItemState.DISABLED
                    ),
                )
                for entry in plan
            ]
            self._by_key = {item.key: item for item in self.items}

    def _ensure_item(self, key: str, label: str, kind: str) -> ItemView:
        # Events can arrive for keys absent from the plan (defensive).
        item = self._by_key.get(key)
        if item is None:
            item = ItemView(
                key=key, label=label, params={}, kind=kind, enabled=True
            )
            self.items.append(item)
            self._by_key[key] = item
        return item

    def on_stage_started(self, key: str, label: str) -> None:
        with self.lock:
            item = self._ensure_item(key, label, "stage")
            item.state = ItemState.RUNNING
            item.started_at = monotonic()
            self.current_stage_key = key

    def on_stage_completed(
        self, key: str, elapsed_seconds: float, result: str | None
    ) -> None:
        with self.lock:
            item = self._by_key.get(key)
            if item is None:
                return
            item.state = ItemState.DONE
            item.elapsed = elapsed_seconds
            if result is not None:
                item.result = result
            if self.current_stage_key == key:
                self.current_stage_key = None

    def on_stage_skipped(self, key: str, reason: str) -> None:
        with self.lock:
            item = self._by_key.get(key)
            if item is None:
                return
            item.state = (
                ItemState.CACHED
                if reason == "already-complete"
                else ItemState.DISABLED
            )

    def on_side_task_started(self, key: str, label: str) -> None:
        with self.lock:
            item = self._ensure_item(key, label, "side_task")
            item.state = ItemState.RUNNING
            item.started_at = monotonic()

    def on_side_task_completed(
        self, key: str, elapsed_seconds: float, result: str | None
    ) -> None:
        with self.lock:
            item = self._by_key.get(key)
            if item is None:
                return
            item.state = ItemState.DONE
            item.elapsed = elapsed_seconds
            if result is not None:
                item.result = result

    def on_side_task_failed(self, key: str, message: str) -> None:
        with self.lock:
            item = self._by_key.get(key)
            if item is None:
                return
            item.state = ItemState.FAILED
            item.error = message
            if item.started_at is not None:
                item.elapsed = monotonic() - item.started_at

    def on_side_task_skipped(self, key: str, reason: str) -> None:
        # Same display semantics as stages.
        self.on_stage_skipped(key, reason)

    def reset_for_new_attempt(self) -> None:
        """Clear per-attempt state before a retry re-runs the pipeline.

        Items are rebuilt by the next ``pipeline_started``; completed stages
        will re-arrive as "already-complete" thanks to resumability.
        """
        with self.lock:
            self.finished = False
            self.failed = False
            self.error = None
            self.finished_at = None
            self.started_at = monotonic()
            self.chunks = ChunkBoard()
            self.current_stage_key = None

    def on_pipeline_completed(self) -> None:
        with self.lock:
            self.finished = True
            self.finished_at = monotonic()

    def on_pipeline_failed(self, message: str) -> None:
        with self.lock:
            self.finished = True
            self.failed = True
            self.error = message
            self.finished_at = monotonic()
            # The stage that was running when the pipeline died is the one
            # that failed.
            if self.current_stage_key is not None:
                item = self._by_key.get(self.current_stage_key)
                if item is not None and item.state is ItemState.RUNNING:
                    item.state = ItemState.FAILED
                    item.error = message
                    if item.started_at is not None:
                        item.elapsed = monotonic() - item.started_at

    # ---------- sub-task bars ----------

    def start_bar(
        self, owner_key: str | None, label: str, total: float
    ) -> TaskID:
        with self.lock:
            task_id = TaskID(self._next_task_id)
            self._next_task_id += 1
            item = self._owner_item(owner_key)
            if item is not None:
                item.bars[task_id] = SubTaskBar(label=label, total=total)
                self._bar_owner[task_id] = item.key
            return task_id

    def advance_bar(
        self, task_id: TaskID, amount: float, description: str | None
    ) -> None:
        with self.lock:
            bar = self._find_bar(task_id)
            if bar is None:
                return
            bar.completed += amount
            if description is not None:
                bar.description = description

    def finish_bar(self, task_id: TaskID, status: str) -> None:
        with self.lock:
            bar = self._find_bar(task_id)
            if bar is None:
                return
            bar.completed = bar.total
            bar.status = status
            bar.done = True

    def _owner_item(self, owner_key: str | None) -> ItemView | None:
        if owner_key is not None and owner_key in self._by_key:
            return self._by_key[owner_key]
        if self.current_stage_key is not None:
            return self._by_key.get(self.current_stage_key)
        return None

    def _find_bar(self, task_id: TaskID) -> SubTaskBar | None:
        owner = self._bar_owner.get(task_id)
        if owner is None:
            return None
        item = self._by_key.get(owner)
        if item is None:
            return None
        return item.bars.get(task_id)

    # ---------- chunk board ----------

    def on_chunk_started(
        self, index: int, total: int, from_index: int, to_index: int
    ) -> None:
        with self.lock:
            self.chunks.total = max(self.chunks.total, total)
            cell = self.chunks.cells.setdefault(index, ChunkCell(index))
            cell.from_index = from_index
            cell.to_index = to_index
            cell.state = ChunkState.ACTIVE
            cell.started_at = monotonic()

    def on_chunk_finished(
        self, index: int, retries: int, cost: float
    ) -> None:
        with self.lock:
            cell = self.chunks.cells.setdefault(index, ChunkCell(index))
            cell.state = ChunkState.DONE
            cell.retries = retries
            cell.cost = cost
            cell.elapsed = cell.live_elapsed()
            self.chunks.retries += retries
            self.chunks.cost += cost

    def on_chunk_failed(
        self, index: int, message: str, retries: int, cost: float
    ) -> None:
        with self.lock:
            cell = self.chunks.cells.setdefault(index, ChunkCell(index))
            cell.state = ChunkState.FAILED
            cell.retries = retries
            cell.cost = cost
            cell.elapsed = cell.live_elapsed()
            self.chunks.retries += retries
            self.chunks.cost += cost

    # ---------- logs ----------

    def append_log(
        self, owner_key: str | None, level: str, text: str
    ) -> None:
        with self.lock:
            item = self._owner_item(owner_key)
            target = item.log if item is not None else self.pipeline_log
            target.append((level, text))
