"""Lifecycle management for workflow background side tasks."""

from concurrent.futures import Future, ThreadPoolExecutor
from time import perf_counter
from types import TracebackType

from loguru import logger

from project import Project
from services.postprocess import (
    apply_date_research_result,
    generate_cover,
    load_cached_date_research,
    research_broadcast_date,
)
from .timing import format_elapsed

# Generous upper bound so slow-but-progressing cover generation still lands.
_COVER_JOIN_TIMEOUT_SECS = 1800

# Web research is normally faster, but keep parity with cover join behavior.
_DATE_RESEARCH_JOIN_TIMEOUT_SECS = 1800


class SideTaskManager:
    """Start and join optional side tasks without changing pipeline errors."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.cover_executor: ThreadPoolExecutor | None = None
        self.cover_future: Future | None = None
        self.cover_started_at: float | None = None
        self.date_executor: ThreadPoolExecutor | None = None
        self.date_future: Future | None = None
        self.date_started_at: float | None = None

    def __enter__(self) -> "SideTaskManager":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        self.join()
        return False

    def apply_cached_date_research_if_available(self) -> None:
        """Apply an already-paid cached date verdict before dispatching work."""
        if (
            self.project.broadcast_date is not None
            or self.project.is_broadcast_date_researched
        ):
            return

        cached_verdict = load_cached_date_research(self.project)
        if cached_verdict is not None:
            apply_date_research_result(self.project, cached_verdict)

    def start_date_research_if_needed(
        self, *, enabled: bool, allow_side_tasks: bool
    ) -> None:
        if (
            not enabled
            or not allow_side_tasks
            or self.project.broadcast_date is not None
            or self.project.is_broadcast_date_researched
        ):
            return

        logger.info(
            f"Stage: Starting async broadcast-date research for "
            f"{self.project.id}"
        )
        self.date_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="date-research"
        )
        self.date_started_at = perf_counter()
        self.date_future = self.date_executor.submit(
            research_broadcast_date, self.project
        )

    def start_cover_if_needed(
        self, *, enabled: bool, allow_side_tasks: bool
    ) -> None:
        if (
            not enabled
            or not allow_side_tasks
            or self.project.is_cover_generated
        ):
            return

        logger.info(
            f"Stage: Starting async cover generation for {self.project.id}"
        )
        self.cover_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="cover"
        )
        self.cover_started_at = perf_counter()
        self.cover_future = self.cover_executor.submit(
            generate_cover, self.project
        )

    def join(self) -> None:
        self._join_cover()
        self._join_date_research()

    def _join_cover(self) -> None:
        if self.cover_future is not None:
            try:
                self.cover_future.result(timeout=_COVER_JOIN_TIMEOUT_SECS)
                self.project.is_cover_generated = True
                self.project.save()
                elapsed = self._elapsed_since(self.cover_started_at)
                logger.success(f"Stage complete: Cover generated ({elapsed})")
            except Exception as cover_error:
                logger.warning(f"Cover generation failed: {cover_error}")
        if self.cover_executor is not None:
            self.cover_executor.shutdown(wait=False)

    def _join_date_research(self) -> None:
        if self.date_future is not None:
            try:
                date_result = self.date_future.result(
                    timeout=_DATE_RESEARCH_JOIN_TIMEOUT_SECS
                )
                apply_date_research_result(self.project, date_result)
                elapsed = self._elapsed_since(self.date_started_at)
                logger.success(
                    f"Stage complete: Broadcast-date research ({elapsed})"
                )
            except Exception as date_error:
                logger.warning(
                    f"Broadcast-date research failed: {date_error}"
                )
        if self.date_executor is not None:
            self.date_executor.shutdown(wait=False)

    @staticmethod
    def _elapsed_since(started_at: float | None) -> str:
        if started_at is None:
            return format_elapsed(0)
        return format_elapsed(perf_counter() - started_at)
