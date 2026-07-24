"""Video download functionality using yt-dlp.

This module handles video downloads from various sources with automatic
thumbnail extraction, metadata embedding, and format conversion.
"""

import os
from time import monotonic, sleep

import yt_dlp
from yt_dlp.postprocessor import FFmpegThumbnailsConvertorPP
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import DownloadError, parse_duration, replace_extension
from loguru import logger
from pathlib import Path
from typing import Any, cast

from settings import settings
from services.progress import NoopProgressReporter

from .client import YtDlpLoguruAdapter, get_ytdlp_download_options_for_url


# Some platforms (notably Abema) regularly fail the first download attempt
# and succeed on the second, so one automatic retry is built in.
_DOWNLOAD_ATTEMPTS = 2
_DOWNLOAD_RETRY_DELAY_SECS = 5


class _ReporterProgressHook:
    """Feed yt-dlp progress into the reporter as one bar per output file.

    Used only for screen-owning reporters (full-screen TUI), where yt-dlp's
    native stdout progress renderer would corrupt the display.
    """

    _MIN_UPDATE_INTERVAL = 0.25  # seconds; yt-dlp fires hooks very frequently

    def __init__(self, progress: NoopProgressReporter) -> None:
        self.progress = progress
        self._task = None
        self._filename: str | None = None
        self._fraction = 0.0
        self._last_update = 0.0

    def __call__(self, event: dict) -> None:
        status = event.get("status")
        filename = event.get("filename") or ""
        if status == "downloading":
            if filename != self._filename:
                self._finish_current()
                self._filename = filename
                self._fraction = 0.0
                self._task = self.progress.start_stage(
                    f"Downloading {os.path.basename(filename)}", total=1.0
                )
            now = monotonic()
            if now - self._last_update < self._MIN_UPDATE_INTERVAL:
                return
            self._last_update = now
            total = (
                event.get("total_bytes")
                or event.get("total_bytes_estimate")
                or 0
            )
            downloaded = event.get("downloaded_bytes") or 0
            if total <= 0:
                return
            fraction = min(1.0, downloaded / total)
            if fraction <= self._fraction:
                return
            speed = event.get("speed")
            eta = event.get("eta")
            description = f"Downloading {os.path.basename(filename)}"
            if speed:
                description += f" · {speed / 1024 / 1024:.1f} MiB/s"
            if eta:
                description += f" · eta {int(eta)}s"
            self.progress.advance(
                self._task, fraction - self._fraction, description=description
            )
            self._fraction = fraction
        elif status == "finished":
            self._finish_current()

    def _finish_current(self) -> None:
        if self._task is not None:
            self.progress.advance(self._task, 1.0 - self._fraction)
            self.progress.finish(self._task, "done")
        self._task = None
        self._filename = None
        self._fraction = 0.0


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
    url: str,
    output_path: Path,
    partial_download: bool = False,
    progress: NoopProgressReporter | None = None,
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
        progress: Optional reporter. When it owns the screen (full-screen
            TUI), yt-dlp's native stdout renderer is replaced with progress
            hooks feeding the reporter; otherwise the native renderer stays.

    Raises:
        DownloadError: If yt-dlp fails to download the video.
        Exception: For unexpected errors during download.
    """
    logger.info(f"Initiating download task for input: {url}")

    for attempt in range(_DOWNLOAD_ATTEMPTS):
        try:
            # Options (and the stateful progress hook) are rebuilt per
            # attempt; yt-dlp resumes partial .part files on its own.
            _download_video_once(url, output_path, partial_download, progress)
            return
        except Exception as e:
            if attempt + 1 < _DOWNLOAD_ATTEMPTS:
                logger.warning(
                    f"Download attempt {attempt + 1} failed for {url}: {e}; "
                    f"retrying in {_DOWNLOAD_RETRY_DELAY_SECS}s"
                )
                sleep(_DOWNLOAD_RETRY_DELAY_SECS)
                continue
            if isinstance(e, DownloadError):
                logger.error(f"yt-dlp download failed for {url}: {e}")
            else:
                logger.error(f"Unexpected error during download execution: {e}")
            raise


def _download_video_once(
    url: str,
    output_path: Path,
    partial_download: bool,
    progress: NoopProgressReporter | None,
) -> None:
    """Build yt-dlp options and run one download attempt."""
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

    if progress is not None and progress.owns_screen:
        # Full-screen reporter: no raw stdout allowed. Route yt-dlp status
        # lines through loguru and progress redraws through reporter hooks.
        ydl_opts.update(
            {
                "logger": YtDlpLoguruAdapter(),
                "noprogress": True,
                "progress_hooks": [_ReporterProgressHook(progress)],
            }
        )

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

    # Execute download; failures propagate to the retry loop in
    # download_video, which owns the error logging.
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
