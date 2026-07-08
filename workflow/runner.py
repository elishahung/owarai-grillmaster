"""Shared stage execution helpers for the resumable workflow."""

from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter

from loguru import logger

from project import Project, ProgressStage
from .timing import format_elapsed


@dataclass(frozen=True)
class StageSpec:
    """Log and progress metadata for one resumable workflow stage."""

    stage: ProgressStage
    start_message: str
    complete_message: str
    skipped_message: str
    on_skip: Callable[[], None] | None = None


class WorkflowRunner:
    """Run project stages with consistent skip, mark, and breakpoint handling."""

    def __init__(
        self,
        *,
        project: Project,
        project_id: str,
        break_after: ProgressStage | None,
    ) -> None:
        self.project = project
        self.project_id = project_id
        self.break_after = break_after

    def run(self, spec: StageSpec, action: Callable[[], None]) -> bool:
        """Run a stage action if incomplete and return whether to stop."""
        if getattr(self.project, spec.stage.value):
            if spec.on_skip is not None:
                spec.on_skip()
            logger.debug(f"Stage skipped: {spec.skipped_message}")
            return self._should_stop_after_stage(spec.stage)

        logger.info(f"Stage: {spec.start_message} for {self.project_id}")
        started_at = perf_counter()
        action()
        self.project.mark_progress(spec.stage)
        elapsed = format_elapsed(perf_counter() - started_at)
        logger.success(f"Stage complete: {spec.complete_message} ({elapsed})")
        return self._should_stop_after_stage(spec.stage)

    def run_optional(
        self,
        *,
        enabled: bool,
        disabled_message: str,
        spec: StageSpec,
        action: Callable[[], None],
    ) -> bool:
        """Run an optional stage when enabled."""
        if not enabled:
            logger.debug(f"Stage skipped: {disabled_message}")
            return False
        return self.run(spec, action)

    def _should_stop_after_stage(self, completed_stage: ProgressStage) -> bool:
        if self.break_after != completed_stage:
            return False

        logger.warning(
            f"Breakpoint reached after {completed_stage.value}; "
            f"stopping project processing: {self.project_id}"
        )
        return True
