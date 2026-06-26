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
    VIDEO_FILE_NAME,
    Project,
)
from services.media import MediaProcessor
from services.package.constants import NOISE_SOURCE_SUFFIX
from services.package.cover import copy_cover
from services.package.remix import package_remix
from services.progress import NoopProgressReporter


def package_project(
    project: Project,
    source_root: Path,
    package_root: Path,
    progress: NoopProgressReporter | None = None,
    remix_noise_name: str | None = None,
    remix_prefix: bool = False,
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

    try:
        if remix_noise_name is None:
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
                noise_name=remix_noise_name,
                prefix_noise=remix_prefix,
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
    remix_prefix: bool = False,
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
        remix_prefix=remix_prefix,
    )


def prepare_noise(
    package_root: Path,
    noise_name: str,
    chunk_duration_seconds: int = 300,
    progress: NoopProgressReporter | None = None,
) -> None:
    """Prepare normalized noise chunks under PACKAGE_PATH/noise."""
    noise_root = package_root / "noise"
    noise_file = noise_root / f"{noise_name}{NOISE_SOURCE_SUFFIX}"
    output_dir = noise_root / noise_name
    MediaProcessor.prepare_noise_chunks(
        noise_file=noise_file,
        output_dir=output_dir,
        chunk_duration_seconds=chunk_duration_seconds,
        progress=progress,
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

    optional_reports = [
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
    ]
    for source, target_name in optional_reports:
        if not source.exists():
            continue
        shutil.copy2(source, target_dir / target_name)
        logger.info(
            f"Copied package artifact: {source} -> {target_dir / target_name}"
        )


def _prepare_target_dir(project: Project, package_root: Path) -> Path:
    target_dir = package_root / f"{project.id}_{project.name}"
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir
