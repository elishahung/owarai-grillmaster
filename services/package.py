"""Final deliverable packaging: subtitle burn-in + cover copy."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from loguru import logger

from project import (
    ASS_FILE_NAME,
    POSTER_COVER_FILE_NAME,
    POSTER_FILE_NAME,
    VIDEO_FILE_NAME,
    Project,
)
from services.media import MediaProcessor
from services.progress import NoopProgressReporter


def package_project(
    project: Project,
    source_root: Path,
    package_root: Path,
    progress: NoopProgressReporter | None = None,
) -> None:
    """Burn subtitles into video and copy the cover into a deliverable folder.

    Best-effort. Logs warnings on failure and never raises into the caller.
    """
    target_dir = package_root / f"{project.id}_{project.name}"
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

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
        MediaProcessor.burn_in_subtitles(
            video_file=video_in,
            subtitle_file=ass_in,
            output_file=target_dir / "video.mp4",
            progress=progress,
        )
    except subprocess.CalledProcessError:
        logger.error("Package skipped: subtitle burn-in failed")
        shutil.rmtree(target_dir, ignore_errors=True)
        return

    cover_src: Path | None = None
    cover_name: str | None = None
    poster_cover = source_root / POSTER_COVER_FILE_NAME
    poster = source_root / POSTER_FILE_NAME
    if poster_cover.exists() and poster_cover.stat().st_size > 0:
        cover_src = poster_cover
        cover_name = "cover.png"
    elif poster.exists() and poster.stat().st_size > 0:
        cover_src = poster
        cover_name = "cover.jpg"

    if cover_src is not None and cover_name is not None:
        shutil.copy2(cover_src, target_dir / cover_name)
        logger.info(f"Copied cover: {cover_src} -> {target_dir / cover_name}")
    else:
        logger.warning(
            f"Package: no cover image found at {poster_cover} or {poster}"
        )

    logger.success(f"Project packaged to {target_dir}")
