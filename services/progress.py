"""Rich-backed progress reporting with optional loguru integration."""

from __future__ import annotations

import os
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class PlannedStage:
    """One entry of the run plan announced via ``pipeline_started``.

    ``key`` is a stable short identifier (e.g. "download", "chunks", "cover")
    used to correlate later stage/side-task events with this plan entry.
    """

    key: str
    label: str
    params: dict[str, str] = field(default_factory=dict)
    kind: str = "stage"  # "stage" | "side_task"
    enabled: bool = True


class NoopProgressReporter:
    """Progress reporter implementation for non-interactive runs and tests.

    Also defines the full reporter contract: subclasses (Rich, TUI, test
    fakes) inherit no-op defaults for every event they do not care about.
    """

    # True only for reporters that own the whole terminal screen (full-screen
    # TUI); services use it to avoid writing raw output to stdout/stderr.
    owns_screen: bool = False

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

    @contextmanager
    def suspend(self):
        yield

    # ---- structured pipeline lifecycle events (all optional to consume) ----

    def pipeline_started(
        self, project: Any, plan: list[PlannedStage]
    ) -> None:
        return None

    def stage_started(self, key: str, label: str) -> None:
        return None

    def stage_completed(
        self, key: str, elapsed_seconds: float, result: str | None = None
    ) -> None:
        return None

    def stage_skipped(self, key: str, reason: str) -> None:
        """``reason`` is "already-complete" (resume) or "disabled"."""
        return None

    def side_task_started(self, key: str, label: str) -> None:
        return None

    def side_task_completed(
        self, key: str, elapsed_seconds: float, result: str | None = None
    ) -> None:
        return None

    def side_task_failed(self, key: str, message: str) -> None:
        return None

    def side_task_skipped(self, key: str, reason: str) -> None:
        """``reason`` is "already-complete" or "disabled"."""
        return None

    def pipeline_completed(self) -> None:
        return None

    def pipeline_failed(self, message: str) -> None:
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
            transient=True,
        )
        self._lock = threading.RLock()
        self._log_sink_id: int | None = None
        self._live_started = False
        self._chunk_task_id: TaskID | None = None
        self._chunk_total = 0
        self._chunk_active = 0
        self._chunk_failed = 0
        self._chunk_retries = 0

    def __enter__(self) -> "RichProgressReporter":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._stop_live()

    def _write_log(self, message: Any) -> None:
        with self._lock:
            self.console.print(str(message), end="")

    def _start_live(self) -> None:
        if self._live_started:
            return
        self.live.start()
        logger.remove()
        self._log_sink_id = logger.add(
            self._write_log,
            colorize=False,
            enqueue=False,
        )
        self._live_started = True

    def _stop_live(self) -> None:
        if not self._live_started:
            return
        if self._log_sink_id is not None:
            logger.remove(self._log_sink_id)
            self._log_sink_id = None
        self.live.stop()
        logger.add(sys.stderr)
        self._live_started = False

    def _get_task(self, task_id: TaskID):
        return next(
            task for task in self.progress.tasks if task.id == task_id
        )

    def start_stage(
        self, label: str, total: float | None = None
    ) -> TaskID | None:
        if total is None:
            return None
        with self._lock:
            self._start_live()
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
            task = self._get_task(task_id)
            completed = task.total if task.total is not None else task.completed
            self.progress.update(task_id, completed=completed, status=status)
            self.progress.stop_task(task_id)
            self.progress.remove_task(task_id)
            self._stop_live()

    @contextmanager
    def suspend(self):
        was_started = self._live_started
        if was_started:
            self._stop_live()
        try:
            yield
        finally:
            if was_started and list(self.progress.tasks):
                self._start_live()

    def chunk_started(
        self, index: int, total: int, from_index: int, to_index: int
    ) -> None:
        with self._lock:
            self._start_live()
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
            and self._get_task(self._chunk_task_id).completed >= self._chunk_total
        ):
            self.progress.stop_task(self._chunk_task_id)
            self.progress.remove_task(self._chunk_task_id)
            self._chunk_task_id = None
            self._chunk_total = 0
            self._chunk_active = 0
            self._chunk_failed = 0
            self._chunk_retries = 0
            self._stop_live()


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
