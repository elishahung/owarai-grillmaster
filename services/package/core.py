"""Package orchestration for finalized projects."""
from __future__ import annotations

import shutil
from pathlib import Path

from loguru import logger

from project import (
    ASS_FILE_NAME,
    GLOSSARY_CHECK_CACHE_DIR_NAME,
    GLOSSARY_CHECK_REPORT_FILE_NAME,
    PROJECT_FILE_NAME,
    PRE_PASS_CACHE_DIR_NAME,
    PRE_PASS_FILE_NAME,
    REFINE_CACHE_DIR_NAME,
    REFINE_REPORT_FILE_NAME,
    TITLES_FILE_NAME,
    VIDEO_FILE_NAME,
    Project,
)
from services.media import MediaProcessor
from services.package.cover import copy_cover
from services.package.rc import resolve_remix_noise_name
from services.package.remix import package_remix
from services.package.titles import ensure_titles, titles_path
from services.progress import NoopProgressReporter


def package_project(
    project: Project,
    source_root: Path,
    package_root: Path,
    progress: NoopProgressReporter | None = None,
    remix_noise_name: str | None = None,
) -> None:
    """Create the deliverable folder.

    Best-effort. Logs warnings on failure and never raises into the caller.
    """
    target_dir = _prepare_target_dir(project, package_root)

    video_in = source_root / VIDEO_FILE_NAME
    ass_in = source_root / ASS_FILE_NAME
    if not video_in.exists():
        logger.warning(f"Package skipped: video not found at {video_in}")
        shutil.rmtree(target_dir, ignore_errors=True)
        return
    if not ass_in.exists():
        logger.warning(f"Package skipped: ASS subtitle not found at {ass_in}")
        shutil.rmtree(target_dir, ignore_errors=True)
        return

    # Titles are packaged like any other artifact, but unlike the reports
    # they are produced here rather than by a pipeline stage — so make sure
    # they exist before the deliverable is built.
    ensure_titles(source_root)

    noise_name = resolve_remix_noise_name(
        requested=remix_noise_name,
        series=project.source_metadata.series,
        channel=project.source_metadata.channel,
    )

    try:
        if noise_name is None:
            MediaProcessor.burn_in_subtitles(
                video_file=video_in,
                subtitle_file=ass_in,
                output_file=target_dir / "video.mp4",
                progress=progress,
            )
        else:
            package_remix(
                source_root=source_root,
                package_root=package_root,
                target_dir=target_dir,
                video_file=video_in,
                subtitle_file=ass_in,
                noise_name=noise_name,
                progress=progress,
            )
    except Exception as e:
        logger.error(f"Package skipped: {e}")
        shutil.rmtree(target_dir, ignore_errors=True)
        return

    copy_cover(source_root, target_dir)
    copy_auxiliary_artifacts(source_root, target_dir)
    logger.success(f"Project packaged to {target_dir}")


def package_project_directory(
    project_dir: Path,
    package_root: Path,
    remix_noise_name: str | None = None,
    progress: NoopProgressReporter | None = None,
) -> None:
    """Package an already-finalized project directory."""
    project_json = project_dir / PROJECT_FILE_NAME
    if not project_json.exists():
        raise FileNotFoundError(f"project.json not found: {project_json}")
    project = Project.model_validate_json(
        project_json.read_text(encoding="utf-8")
    )
    package_project(
        project=project,
        source_root=project_dir,
        package_root=package_root,
        progress=progress,
        remix_noise_name=remix_noise_name,
    )


def copy_auxiliary_artifacts(source_root: Path, target_dir: Path) -> None:
    """Copy analysis artifacts into a package directory."""
    required_pre_pass = source_root / PRE_PASS_CACHE_DIR_NAME / PRE_PASS_FILE_NAME
    if required_pre_pass.exists():
        shutil.copy2(required_pre_pass, target_dir / PRE_PASS_FILE_NAME)
        logger.info(
            f"Copied package artifact: "
            f"{required_pre_pass} -> {target_dir / PRE_PASS_FILE_NAME}"
        )
    else:
        logger.warning(f"Package: pre-pass JSON not found at {required_pre_pass}")

    optional_artifacts = [
        (
            source_root / REFINE_CACHE_DIR_NAME / REFINE_REPORT_FILE_NAME,
            "refine.md",
        ),
        (
            source_root
            / GLOSSARY_CHECK_CACHE_DIR_NAME
            / GLOSSARY_CHECK_REPORT_FILE_NAME,
            "glossary_check.md",
        ),
        (titles_path(source_root), TITLES_FILE_NAME),
    ]
    for source, target_name in optional_artifacts:
        if not source.exists():
            continue
        shutil.copy2(source, target_dir / target_name)
        logger.info(
            f"Copied package artifact: {source} -> {target_dir / target_name}"
        )


def _prepare_target_dir(project: Project, package_root: Path) -> Path:
    target_dir = project.package_dir(package_root)
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir
