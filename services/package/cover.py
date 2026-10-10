"""Cover image selection and copy helpers for package output."""

from __future__ import annotations

import shutil
from pathlib import Path

from loguru import logger

from project import POSTER_COVER_FILE_NAME, POSTER_FILE_NAME


def copy_cover(source_root: Path, target_dir: Path) -> None:
    """Copy the best available cover image into a package directory."""
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
        logger.warning(f"Package: no cover image found at {poster_cover} or {poster}")
