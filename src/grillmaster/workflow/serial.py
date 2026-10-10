"""Serial multi-project runs: each project seeds the next one's pre-pass."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from grillmaster.project import Project
from grillmaster.services.progress import NoopProgressReporter

from .api import submit_project


@dataclass
class SerialRun:
    """Process sources in order, chaining each final directory as the next parent.

    Stateful on purpose: the dashboard's retry re-enters ``run`` and continues
    from the failed project (which is itself resumable) instead of restarting
    the chain — earlier projects have already been archived away.
    """

    sources: list[str]
    parent_project_path: Path | None = None
    submit_kwargs: dict[str, Any] = field(default_factory=dict)
    position: int = 0

    def __post_init__(self) -> None:
        # Fail on a bad or repeated source before the first project spends
        # anything.
        if not self.sources:
            raise ValueError("At least one source is required")
        ids = [Project.parse_source_str(source) for source in self.sources]
        duplicates = sorted({vid for vid in ids if ids.count(vid) > 1})
        if duplicates:
            raise ValueError(f"Duplicate sources: {', '.join(duplicates)}")

    def run(self, progress: NoopProgressReporter | None = None) -> Path | None:
        """Run the remaining projects; returns the last final directory."""
        total = len(self.sources)
        while self.position < total:
            source = self.sources[self.position]
            if progress is not None:
                progress.batch_item_started(self.position + 1, total, source)
            try:
                final_path = submit_project(
                    source_str=source,
                    parent_project_path=self.parent_project_path,
                    progress=progress,
                    **self.submit_kwargs,
                )
            except Exception:
                logger.error(
                    f"Serial run stopped at {source}. Resume with the "
                    f"original flags plus: {self.resume_command()}"
                )
                raise
            self.parent_project_path = final_path
            self.position += 1
            logger.success(
                f"Serial {self.position}/{total} complete: {source} -> {final_path}"
            )
        return self.parent_project_path

    def resume_command(self) -> str:
        """CLI line that continues the chain from the current position."""
        parts = ["grill", "serial", *self.sources[self.position :]]
        if self.parent_project_path is not None:
            parts += ["--parent-project", str(self.parent_project_path)]
        return " ".join(f'"{p}"' if " " in p else p for p in parts)
