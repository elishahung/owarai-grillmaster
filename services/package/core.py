"""Package orchestration for finalized projects."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from loguru import logger

from project import (
    ASS_FILE_NAME,
    GLOSSARY_CHECK_CACHE_DIR_NAME,
    GLOSSARY_CHECK_REPORT_FILE_NAME,
    INFO_FILE_NAME,
    PROJECT_FILE_NAME,
    PRE_PASS_CACHE_DIR_NAME,
    PRE_PASS_FILE_NAME,
    REFINE_CACHE_DIR_NAME,
    REFINE_REPORT_FILE_NAME,
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

    # Everything that is a plain file copy lands first: the video render is
    # the long step, and a deliverable folder that already carries its cover
    # and analysis artifacts is inspectable while ffmpeg is still running.
    copy_cover(source_root, target_dir)
    copy_auxiliary_artifacts(source_root, target_dir)

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
    write_info(source_root, target_dir)

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
    ]
    for source, target_name in optional_artifacts:
        if not source.exists():
            continue
        shutil.copy2(source, target_dir / target_name)
        logger.info(
            f"Copied package artifact: {source} -> {target_dir / target_name}"
        )


def write_info(source_root: Path, target_dir: Path) -> None:
    """Merge the pre-pass briefing and title suggestions into `info.json`.

    Titles lead the file — they are what a human reads first — and the
    pre-pass fields follow in their own order. Either half may be missing;
    only an empty merge writes nothing.
    """
    info: dict[str, object] = {}
    titles = _read_json_object(titles_path(source_root))
    if titles is not None:
        info.update(titles)

    pre_pass_file = source_root / PRE_PASS_CACHE_DIR_NAME / PRE_PASS_FILE_NAME
    pre_pass = _read_json_object(pre_pass_file)
    if pre_pass is None:
        logger.warning(f"Package: pre-pass JSON unavailable at {pre_pass_file}")
    else:
        info.update(pre_pass)

    if not info:
        return
    target = target_dir / INFO_FILE_NAME
    target.write_text(
        json.dumps(info, ensure_ascii=False, indent=4), encoding="utf-8"
    )
    logger.info(f"Wrote package artifact: {target}")


def _read_json_object(path: Path) -> dict[str, object] | None:
    """Load a JSON object; anything unusable counts as absent."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logger.warning(f"Package: ignoring unreadable JSON ({path}): {error}")
        return None
    if not isinstance(data, dict):
        logger.warning(f"Package: ignoring non-object JSON ({path})")
        return None
    return data


def _prepare_target_dir(project: Project, package_root: Path) -> Path:
    target_dir = project.package_dir(package_root)
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir
