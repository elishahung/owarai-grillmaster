"""Rich-backed progress reporting with optional loguru integration."""

from __future__ import annotations

import os
import sys
import threading
from types import TracebackType
from typing import Any

from loguru import logger
from rich.console import Console
from rich.live import Live
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)


class NoopProgressReporter:
    """Progress reporter implementation for non-interactive runs and tests."""

    def __enter__(self) -> "NoopProgressReporter":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def start_stage(
        self, label: str, total: float | None = None
    ) -> TaskID | None:
        return None

    def advance(
        self,
        task_id: TaskID | None,
        amount: float = 1.0,
        description: str | None = None,
    ) -> None:
        return None

    def finish(
        self, task_id: TaskID | None, status: str = "done"
    ) -> None:
        return None

    def chunk_started(
        self, index: int, total: int, from_index: int, to_index: int
    ) -> None:
        return None

    def chunk_finished(self, index: int, retries: int, cost: float) -> None:
        return None

    def chunk_failed(
        self, index: int, message: str, retries: int = 0, cost: float = 0.0
    ) -> None:
        return None


class RichProgressReporter(NoopProgressReporter):
    """Rich progress reporter that keeps loguru output above live tasks."""

    def __init__(self, console: Console | None = None):
        self.console = console or Console()
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("{task.fields[status]}"),
            TimeElapsedColumn(),
            console=self.console,
            expand=True,
        )
        self.live = Live(
            self.progress,
            console=self.console,
            refresh_per_second=8,
            transient=False,
        )
        self._lock = threading.RLock()
        self._log_sink_id: int | None = None
        self._chunk_task_id: TaskID | None = None
        self._chunk_total = 0
        self._chunk_active = 0
        self._chunk_failed = 0
        self._chunk_retries = 0

    def __enter__(self) -> "RichProgressReporter":
        self.live.start()
        logger.remove()
        self._log_sink_id = logger.add(
            self._write_log,
            colorize=False,
            enqueue=False,
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._log_sink_id is not None:
            logger.remove(self._log_sink_id)
            self._log_sink_id = None
        self.live.stop()
        logger.add(sys.stderr)

    def _write_log(self, message: Any) -> None:
        with self._lock:
            self.console.print(str(message), end="")

    def start_stage(
        self, label: str, total: float | None = None
    ) -> TaskID | None:
        with self._lock:
            return self.progress.add_task(
                label,
                total=total,
                status="",
            )

    def advance(
        self,
        task_id: TaskID | None,
        amount: float = 1.0,
        description: str | None = None,
    ) -> None:
        if task_id is None:
            return
        update: dict[str, Any] = {"advance": amount}
        if description is not None:
            update["description"] = description
        with self._lock:
            self.progress.update(task_id, **update)

    def finish(
        self, task_id: TaskID | None, status: str = "done"
    ) -> None:
        if task_id is None:
            return
        with self._lock:
            task = self.progress.tasks[task_id]
            completed = task.total if task.total is not None else task.completed
            self.progress.update(task_id, completed=completed, status=status)
            self.progress.stop_task(task_id)

    def chunk_started(
        self, index: int, total: int, from_index: int, to_index: int
    ) -> None:
        with self._lock:
            if self._chunk_task_id is None:
                self._chunk_total = total
                self._chunk_task_id = self.progress.add_task(
                    "Translating chunks",
                    total=total,
                    status="active=0 failed=0 retries=0",
                )
            self._chunk_active += 1
            self._update_chunk_status(
                f"active={self._chunk_active} failed={self._chunk_failed} "
                f"retries={self._chunk_retries} current={index + 1} "
                f"({from_index}-{to_index})"
            )

    def chunk_finished(self, index: int, retries: int, cost: float) -> None:
        with self._lock:
            self._chunk_active = max(0, self._chunk_active - 1)
            self._chunk_retries += retries
            if self._chunk_task_id is not None:
                self.progress.update(self._chunk_task_id, advance=1)
            self._update_chunk_status(
                f"active={self._chunk_active} failed={self._chunk_failed} "
                f"retries={self._chunk_retries} last={index + 1} "
                f"${cost:.4f}"
            )

    def chunk_failed(
        self, index: int, message: str, retries: int = 0, cost: float = 0.0
    ) -> None:
        with self._lock:
            self._chunk_active = max(0, self._chunk_active - 1)
            self._chunk_failed += 1
            self._chunk_retries += retries
            if self._chunk_task_id is not None:
                self.progress.update(self._chunk_task_id, advance=1)
            self._update_chunk_status(
                f"active={self._chunk_active} failed={self._chunk_failed} "
                f"retries={self._chunk_retries} last_failed={index + 1}"
            )

    def _update_chunk_status(self, status: str) -> None:
        if self._chunk_task_id is None:
            return
        self.progress.update(self._chunk_task_id, status=status)
        if (
            self._chunk_total > 0
            and self.progress.tasks[self._chunk_task_id].completed
            >= self._chunk_total
        ):
            self.progress.stop_task(self._chunk_task_id)


def create_progress_reporter() -> NoopProgressReporter | RichProgressReporter:
    """Create an auto-enabled progress reporter for the current process."""
    console = Console()
    if (
        console.is_terminal
        and not os.environ.get("CI")
        and not os.environ.get("NO_COLOR")
    ):
        return RichProgressReporter(console)
    return NoopProgressReporter()
