"""A yt-dlp post-processor for mislabeled thumbnails; importing it loads
`yt_dlp`, so `sources.ytdlp` imports it only when a download starts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import replace_extension

_JPEG_MAGIC = b"\xff\xd8\xff"


class JpegThumbnailFixupPP(PostProcessor):
    """Rename thumbnails whose bytes are JPEG but whose extension is not.

    ABEMA slot (live archive) thumbnails are JPEG bytes under a `.png` name.
    `FFmpegThumbnailsConvertorPP` forces the image2 demuxer, which picks the
    decoder from the extension, so converting the mislabeled file fails with
    "Conversion failed!". Mirrors yt-dlp's own webp fixup, which skips JPEG.
    """

    def run(self, information: Any) -> tuple[list[str], Any]:
        for thumbnail in information.get("thumbnails") or []:
            filepath = thumbnail.get("filepath")
            if not filepath or not Path(filepath).exists():
                continue
            if Path(filepath).suffix.lower() in {".jpg", ".jpeg"}:
                continue
            with Path(filepath).open("rb") as handle:
                if handle.read(3) != _JPEG_MAGIC:
                    continue
            logger.info(f"Correcting thumbnail {filepath} extension to jpg")
            jpg_filepath = replace_extension(filepath, "jpg")
            Path(filepath).replace(jpg_filepath)
            thumbnail["filepath"] = jpg_filepath
            files_to_move = information.get("__files_to_move") or {}
            if filepath in files_to_move:
                files_to_move[jpg_filepath] = replace_extension(
                    files_to_move.pop(filepath), "jpg"
                )
        return [], information
