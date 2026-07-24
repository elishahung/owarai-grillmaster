"""Progress-reporter bridge feeding the TUI's PipelineState.

The pipeline (and its side-task / asyncio worker threads) calls the standard
reporter protocol; this implementation translates every event into a mutation
of the shared ``PipelineState``. Attribution rule for bars and log lines: the
side-task executor threads are named ``date-research*`` / ``cover*``, so
anything logged from them belongs to that side task; everything else belongs
to the currently running stage.
"""

from __future__ import annotations

import sys
import threading
from typing import Any

from loguru import logger
from rich.progress import TaskID

from services.progress import NoopProgressReporter, PlannedStage

from .state import PipelineState

_LOG_FORMAT = "{time:HH:mm:ss} {level.name[0]} {message}"


def owner_key_for_thread_name(name: str) -> str | None:
    """Map a thread name to a side-task key, or None for the main pipeline."""
    if name.startswith("date-research"):
        return "date"
    if name.startswith("cover"):
        return "cover"
    return None


class TuiProgressReporter(NoopProgressReporter):
    """Reporter that renders nothing itself — the Textual app renders state."""

    owns_screen = True

    def __init__(self, state: PipelineState) -> None:
        self.state = state
        self._log_sink_id: int | None = None

    def _owner_key(self) -> str | None:
        return owner_key_for_thread_name(threading.current_thread().name)

    # ---------- loguru routing ----------

    def install_logging(self) -> None:
        """Route all loguru output into per-item log buffers."""
        logger.remove()
        self._log_sink_id = logger.add(
            self._log_sink,
            colorize=False,
            format=_LOG_FORMAT,
            enqueue=False,
        )

    def restore_logging(self) -> None:
        if self._log_sink_id is not None:
            logger.remove(self._log_sink_id)
            self._log_sink_id = None
        logger.add(sys.stderr)

    def _log_sink(self, message: Any) -> None:
        record = message.record
        owner = owner_key_for_thread_name(record["thread"].name)
        self.state.append_log(
            owner, record["level"].name, str(message).rstrip("\n")
        )

    # ---------- legacy bar protocol ----------

    def start_stage(
        self, label: str, total: float | None = None
    ) -> TaskID | None:
        if total is None:
            return None
        return self.state.start_bar(self._owner_key(), label, total)

    def advance(
        self,
        task_id: TaskID | None,
        amount: float = 1.0,
        description: str | None = None,
    ) -> None:
        if task_id is None:
            return
        self.state.advance_bar(task_id, amount, description)

    def finish(self, task_id: TaskID | None, status: str = "done") -> None:
        if task_id is None:
            return
        self.state.finish_bar(task_id, status)

    # suspend() stays the inherited no-op context manager: nothing owns the
    # raw terminal cursor, so blocking subprocesses need no special handling.

    # ---------- structured lifecycle ----------

    def pipeline_started(
        self, project: Any, plan: list[PlannedStage]
    ) -> None:
        self.state.on_pipeline_started(project, plan)

    def stage_started(self, key: str, label: str) -> None:
        self.state.on_stage_started(key, label)

    def stage_completed(
        self, key: str, elapsed_seconds: float, result: str | None = None
    ) -> None:
        self.state.on_stage_completed(key, elapsed_seconds, result)

    def stage_skipped(self, key: str, reason: str) -> None:
        self.state.on_stage_skipped(key, reason)

    def side_task_started(self, key: str, label: str) -> None:
        self.state.on_side_task_started(key, label)

    def side_task_completed(
        self, key: str, elapsed_seconds: float, result: str | None = None
    ) -> None:
        self.state.on_side_task_completed(key, elapsed_seconds, result)

    def side_task_failed(self, key: str, message: str) -> None:
        self.state.on_side_task_failed(key, message)

    def side_task_skipped(self, key: str, reason: str) -> None:
        self.state.on_side_task_skipped(key, reason)

    def pipeline_completed(self) -> None:
        self.state.on_pipeline_completed()

    def pipeline_failed(self, message: str) -> None:
        self.state.on_pipeline_failed(message)

    # ---------- chunk board ----------

    def chunk_started(
        self, index: int, total: int, from_index: int, to_index: int
    ) -> None:
        self.state.on_chunk_started(index, total, from_index, to_index)

    def chunk_finished(self, index: int, retries: int, cost: float) -> None:
        self.state.on_chunk_finished(index, retries, cost)

    def chunk_failed(
        self, index: int, message: str, retries: int = 0, cost: float = 0.0
    ) -> None:
        self.state.on_chunk_failed(index, message, retries, cost)
