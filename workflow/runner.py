"""Shared stage execution helpers for the resumable workflow."""

from collections.abc import Callable
from dataclasses import dataclass, field
from time import perf_counter

from loguru import logger

from project import Project, ProgressStage
from services.progress import NoopProgressReporter
from .timing import format_elapsed


@dataclass(frozen=True)
class StageSpec:
    """Log and progress metadata for one resumable workflow stage."""

    stage: ProgressStage
    key: str
    start_message: str
    complete_message: str
    skipped_message: str
    on_skip: Callable[[], None] | None = None
    params: dict[str, str] = field(default_factory=dict)
    # False only for an opt-in stage this run leaves off (the `--chat` pair).
    enabled: bool = True


class WorkflowRunner:
    """Run project stages with consistent skip, mark, and breakpoint handling."""

    def __init__(
        self,
        *,
        project: Project,
        project_id: str,
        break_after: ProgressStage | None,
        progress: NoopProgressReporter | None = None,
    ) -> None:
        self.project = project
        self.project_id = project_id
        self.break_after = break_after
        self.progress = progress if progress is not None else NoopProgressReporter()

    def run(self, spec: StageSpec, action: Callable[[], None]) -> bool:
        """Run a stage action if enabled and incomplete; return whether to stop."""
        if not spec.enabled:
            logger.debug(f"Stage skipped: {spec.key} disabled for this run")
            self.progress.stage_skipped(spec.key, "disabled")
            return False
        if getattr(self.project, spec.stage.value):
            if spec.on_skip is not None:
                spec.on_skip()
            logger.debug(f"Stage skipped: {spec.skipped_message}")
            self.progress.stage_skipped(spec.key, "already-complete")
            return self._should_stop_after_stage(spec.stage)

        logger.info(f"Stage: {spec.start_message} for {self.project_id}")
        self.progress.stage_started(spec.key, spec.start_message)
        started_at = perf_counter()
        action()
        self.project.mark_progress(spec.stage)
        elapsed_seconds = perf_counter() - started_at
        self.progress.stage_completed(spec.key, elapsed_seconds)
        elapsed = format_elapsed(elapsed_seconds)
        logger.success(f"Stage complete: {spec.complete_message} ({elapsed})")
        return self._should_stop_after_stage(spec.stage)

    def _should_stop_after_stage(self, completed_stage: ProgressStage) -> bool:
        if self.break_after != completed_stage:
            return False

        logger.warning(
            f"Breakpoint reached after {completed_stage.value}; "
            f"stopping project processing: {self.project_id}"
        )
        return True
