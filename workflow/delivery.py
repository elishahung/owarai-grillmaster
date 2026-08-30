"""Post-pipeline archive and package delivery."""

from pathlib import Path
from time import perf_counter

from loguru import logger

from project import Project
from services.package import package_project
from services.progress import NoopProgressReporter
from settings import settings


def deliver_project(
    *,
    project: Project,
    project_id: str,
    progress: NoopProgressReporter,
    remix_noise_name: str | None,
) -> None:
    """Archive and package a completed project."""
    logger.success(f"Project processing complete: {project_id}")

    archived_location: Path | None = None
    if settings.archived_path is not None:
        progress.stage_started("archive", "Archiving project")
        started_at = perf_counter()
        archived_location = project.archive()
        progress.stage_completed(
            "archive", perf_counter() - started_at, str(archived_location)
        )
    else:
        logger.warning("Archived path is not set, skipping archiving")

    if settings.package_path is not None:
        source_root = archived_location or project.project_path
        progress.stage_started("package", "Packaging deliverable")
        started_at = perf_counter()
        package_project(
            project,
            source_root,
            settings.package_path,
            progress,
            remix_noise_name=remix_noise_name,
        )
        progress.stage_completed("package", perf_counter() - started_at)

    logger.info(
        f"Project {project_id} total accumulated API cost: "
        f"${project.total_cost:.4f}"
    )
