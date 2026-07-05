"""Video download functionality using yt-dlp.

This module handles video downloads from various sources with automatic
thumbnail extraction, metadata embedding, and format conversion.
"""

from yt_dlp.utils import DownloadError, parse_duration
from loguru import logger
from pathlib import Path

from .client import get_ytdlp_client


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
        "postprocessors": [
            {
                "key": "FFmpegThumbnailsConvertor",
                "format": "jpg",
                "when": "before_dl",
            },
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

    # Execute download
    try:
        logger.info(f"Starting yt-dlp process for: {url}")

        with get_ytdlp_client(ydl_opts) as ydl:
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
