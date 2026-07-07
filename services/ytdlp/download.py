"""Video download functionality using yt-dlp.

This module handles video downloads from various sources with automatic
thumbnail extraction, metadata embedding, and format conversion.
"""

import os

import yt_dlp
from yt_dlp.postprocessor import FFmpegThumbnailsConvertorPP
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import DownloadError, parse_duration, replace_extension
from loguru import logger
from pathlib import Path
from typing import Any, cast

from settings import settings

from .client import get_ytdlp_download_options_for_url


class _JpegThumbnailFixupPP(PostProcessor):
    """Rename thumbnails whose bytes are JPEG but whose extension is not.

    Abema slot (live archive) thumbnails are served as JPEG bytes under a
    `.png` filename. FFmpegThumbnailsConvertorPP forces the image2 demuxer,
    which picks the decoder from the file extension, so converting the
    mislabeled file fails with "Conversion failed!". Mirrors yt-dlp's own
    `fixup_webp` handling, which covers webp but not jpeg.
    """

    def run(self, info):
        for thumbnail in info.get("thumbnails") or []:
            filepath = thumbnail.get("filepath")
            if not filepath or not os.path.exists(filepath):
                continue
            if os.path.splitext(filepath)[1].lower() in (".jpg", ".jpeg"):
                continue
            with open(filepath, "rb") as f:
                if f.read(3) != b"\xff\xd8\xff":
                    continue
            self.to_screen(
                f'Correcting thumbnail "{filepath}" extension to jpg'
            )
            jpg_filepath = replace_extension(filepath, "jpg")
            os.replace(filepath, jpg_filepath)
            thumbnail["filepath"] = jpg_filepath
            files_to_move = info.get("__files_to_move") or {}
            if filepath in files_to_move:
                files_to_move[jpg_filepath] = replace_extension(
                    files_to_move.pop(filepath), "jpg"
                )
        return [], info


def parse_section_time(value: str) -> float:
    """Parse a section boundary time string into seconds.

    Accepts plain seconds ("90"), MM:SS ("1:30"), HH:MM:SS ("0:01:30"),
    or duration shorthand ("1h30m") via yt-dlp's duration parser.

    Raises:
        ValueError: If the value cannot be parsed or is negative.
    """
    seconds = parse_duration(value)
    if seconds is None or seconds < 0:
        raise ValueError(f"Invalid time value: {value!r}")
    return float(seconds)


def download_video(
    url: str, output_path: Path, partial_download: bool = False
) -> None:
    """Download a video from the given URL using yt-dlp.

    Downloads the video with best available quality, extracts and embeds
    thumbnail, and writes metadata. Output files are organized in the
    specified output directory.

    Args:
        url: The video URL or identifier to download.
        output_path: Directory path where downloaded files will be saved.
        partial_download: If True, enables concurrent fragment downloads
            for faster partial downloads.

    Raises:
        DownloadError: If yt-dlp fails to download the video.
        Exception: For unexpected errors during download.
    """
    logger.info(f"Initiating download task for input: {url}")

    # Configure yt-dlp options
    ydl_opts = {
        "writethumbnail": True,
        "writeinfojson": True,
        "outtmpl": {
            "default": f"{output_path}/%(playlist_index|0)s.%(ext)s",
            "infojson": f"{output_path}/metadata",
            "thumbnail": f"{output_path}/poster",
        },
        "merge_output_format": "mp4",
        "format": "bestvideo+bestaudio/best",
        # NB: the thumbnail convertor is not declared here — it is added via
        # add_post_processor below so the jpeg-extension fixup can run first.
        "postprocessors": [
            {
                # Embed thumbnail into the video file
                "key": "EmbedThumbnail",
                "already_have_thumbnail": True,
            },
            {
                # Write metadata to the video file tags
                "key": "FFmpegMetadata",
                "add_chapters": True,
                "add_metadata": True,
            },
        ],
        "concurrent_fragment_downloads": 8 if partial_download else 1,
    }

    if settings.enable_official_subtitles:
        # Best-effort platform closed captions (TVer/Abema/... 字幕放送).
        # Manual subs only — writeautomaticsub stays off so YouTube
        # auto-captions are never fetched. Programs without CC simply
        # produce no subtitle file. yt-dlp names subtitles `<part>.<lang>.srt`
        # (e.g. `0.ja.srt`) next to the numbered video parts —
        # Project.downloaded_subtitle_paths and normalize_official_subtitle
        # rely on that shape.
        ydl_opts.update(
            {
                "writesubtitles": True,
                "subtitleslangs": ["ja", "ja-*"],
                "subtitlesformat": "vtt/best",
            }
        )
        ydl_opts["postprocessors"] = [
            *ydl_opts["postprocessors"],
            {"key": "FFmpegSubtitlesConvertor", "format": "srt"},
        ]

    # Execute download
    try:
        logger.info(f"Starting yt-dlp process for: {url}")

        download_opts = get_ytdlp_download_options_for_url(url, ydl_opts)
        with yt_dlp.YoutubeDL(cast(Any, download_opts)) as ydl:
            # Fixup must precede the convertor within the before_dl chain;
            # opts-declared postprocessors always run before ones added here,
            # so both are registered via add_post_processor.
            ydl.add_post_processor(_JpegThumbnailFixupPP(), when="before_dl")
            ydl.add_post_processor(
                FFmpegThumbnailsConvertorPP(format="jpg"), when="before_dl"
            )
            # extract_info with download=True performs the download
            info_dict = ydl.extract_info(url, download=True)

            # Safely get title for logging
            video_title = (
                info_dict.get("title", "Unknown Title")
                if info_dict
                else "Unknown"
            )

        logger.success(f"Successfully downloaded: {video_title}")

    except DownloadError as e:
        logger.error(f"yt-dlp download failed for {url}: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during download execution: {e}")
        raise
