"""yt-dlp: shared options, metadata extraction and the video download
(parts joined into one file).

Everything goes through the injectable `YtDlp` seam (`YtDlpLibrary` is the
real one), so tests never run a live extraction. yt-dlp's own output
always goes to loguru and its progress to events: no raw stdout ever
reaches a screen the TUI owns. The `yt_dlp` package loads on first use, so
importing the stage registry stays light.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from loguru import logger
from pydantic import BaseModel, ConfigDict, field_validator

from grillmaster.core.fs import atomic_write_text
from grillmaster.events.types import ProgressAdvanced, ProgressFinished, ProgressStarted
from grillmaster.media.audio import find_audio_gaps
from grillmaster.media.video import join_parts
from grillmaster.sources.base import CookiePolicy
from grillmaster.sources.errors import SourceError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from yt_dlp.postprocessor.common import PostProcessor

    from grillmaster.events.bus import EventSink
    from grillmaster.media.ffmpeg import FfmpegRunner
    from grillmaster.sources.base import SourcePlatform

# Exponential backoff capped at FRAGMENT_RETRY_MAX_SLEEP_S rides out a network
# switch of a few minutes (1+2+4+8+16+30*5 ≈ 3 min over ten retries).
FRAGMENT_RETRIES = 10
FRAGMENT_RETRY_MAX_SLEEP_S = 30.0
# Platform captions: manual subs only (YouTube auto-captions never), any
# Japanese variant, converted to SRT. yt-dlp names them `<part>.<lang>.srt`
# next to the numbered video parts.
CAPTION_LANGUAGES = ("ja", "ja-*")
_PART_NAME = re.compile(r"\d+\.mp4")
_PROGRESS_INTERVAL_S = 0.25  # yt-dlp fires hooks far more often
_MIB = 1024 * 1024


def fragment_retry_sleep(attempt: int) -> float:
    return min(2.0**attempt, FRAGMENT_RETRY_MAX_SLEEP_S)


class YtDlp(Protocol):
    def extract_info(
        self,
        url: str,
        options: Mapping[str, Any],
        *,
        download: bool,
        before_download: Sequence[PostProcessor] = (),
    ) -> dict[str, Any]:
        """Run yt-dlp on `url` and return the JSON-safe info dict.

        `before_download` post-processors run, in order, after every
        options-declared `before_dl` one (yt-dlp registers those first).
        yt-dlp's `DownloadError` propagates.
        """
        ...


class YtDlpLibrary:
    """`YtDlp` over the `yt_dlp` package, one `YoutubeDL` per call."""

    def extract_info(
        self,
        url: str,
        options: Mapping[str, Any],
        *,
        download: bool,
        before_download: Sequence[PostProcessor] = (),
    ) -> dict[str, Any]:
        import yt_dlp  # noqa: PLC0415 - heavy, loaded on first use

        with yt_dlp.YoutubeDL(dict(options)) as ydl:  # pyright: ignore[reportArgumentType] - stubs want a TypedDict
            for processor in before_download:
                ydl.add_post_processor(processor, when="before_dl")
            info = ydl.extract_info(url, download=download)
            if info is None:
                raise SourceError(f"yt-dlp returned no info for {url}")
            sanitized: dict[str, Any] = ydl.sanitize_info(info)  # pyright: ignore[reportAssignmentType] - stubs return a TypedDict
            return sanitized


class LoguruYtDlpLogger:
    """yt-dlp's logger interface, forwarding to loguru with a `[yt-dlp]` prefix."""

    def debug(self, msg: str) -> None:
        # yt-dlp routes info-level lines through `debug` without the prefix.
        if not msg.startswith("[debug] "):
            logger.debug(f"[yt-dlp] {msg}")

    def info(self, msg: str) -> None:
        logger.info(f"[yt-dlp] {msg}")

    def warning(self, msg: str) -> None:
        logger.warning(f"[yt-dlp] {msg}")

    def error(self, msg: str) -> None:
        logger.error(f"[yt-dlp] {msg}")


def shared_options(platform: SourcePlatform, cookies: Path | None) -> dict[str, Any]:
    """Options every yt-dlp run shares: logging, no stdout progress, cookies.

    An anonymous platform never sends cookies (observed on BiliBili
    BV16D4y1H7Wk: signed-in cookies capped the format list at 480p while
    anonymous access got 1080p).
    """
    send_cookies = platform.cookie_policy is CookiePolicy.CONFIGURED and cookies
    return {
        "logger": LoguruYtDlpLogger(),
        "noprogress": True,
        "cookiefile": str(cookies) if send_cookies else None,
    }


class VideoInfo(BaseModel):
    """The fields of yt-dlp's info the pipeline reads.

    `timestamp` is the platform publication epoch (YouTube publish time,
    BiliBili pubdate); `release_timestamp` the release/availability start
    when the platform tells them apart (YouTube premieres, TVer 配信開始);
    `upload_date` is `YYYYMMDD`. `series` is the program name, `channel`
    the station or uploader; platforms expose one, the other, or neither.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str
    title: str
    description: str | None = None
    timestamp: int | None = None
    release_timestamp: int | None = None
    upload_date: str | None = None
    series: str | None = None
    channel: str | None = None

    @field_validator("timestamp", "release_timestamp", mode="before")
    @classmethod
    def _coerce_epoch(cls, value: object) -> int | None:
        # Extractors usually emit int epochs but do not guarantee it; these
        # only feed the best-effort broadcast date, so an odd value is absent.
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return int(value)

    @field_validator("series", "channel", mode="before")
    @classmethod
    def _normalize_label(cls, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        return value.strip() or None

    @property
    def filename(self) -> str:
        """The title reduced to word characters, joined by underscores."""
        safe = re.sub(r"[^\w\s-]", "", self.title).strip()
        return re.sub(r"[-\s]+", "_", safe)


def fetch_info(
    ytdlp: YtDlp, url: str, info_path: Path, *, options: Mapping[str, Any]
) -> VideoInfo:
    """Extract metadata without downloading; keep yt-dlp's info at `info_path`."""
    logger.info(f"Extracting video info: {url}")
    info = ytdlp.extract_info(url, options, download=False)
    atomic_write_text(info_path, json.dumps(info, ensure_ascii=False, indent=2) + "\n")
    video = VideoInfo.model_validate(info)
    logger.success(f"Extracted video info: {video.title}")
    return video


def download_video(
    ytdlp: YtDlp,
    url: str,
    *,
    parts_dir: Path,
    poster: Path,
    options: Mapping[str, Any],
    captions: bool,
    events: EventSink,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Download the best video+audio as numbered parts into `parts_dir`.

    Parts are `<playlist index>.mp4` (yt-dlp zero-pads the index, so name
    order is playlist order) with the thumbnail embedded; the thumbnail is
    also kept as `poster` (a `.jpg`). A missing fragment aborts the download
    after backoff retries instead of leaving a timestamp gap that drifts ASR
    from the video; a re-run resumes from it. With `captions`, platform
    closed captions land beside the parts (see `official_subs`).
    """
    from yt_dlp.postprocessor.ffmpeg import (  # noqa: PLC0415 - heavy
        FFmpegThumbnailsConvertorPP,
    )

    from grillmaster.sources.thumbnails import (  # noqa: PLC0415 - imports yt_dlp
        JpegThumbnailFixupPP,
    )

    if poster.suffix != ".jpg":
        raise ValueError(f"The poster is converted to JPEG: {poster}")
    progress = DownloadProgress(events, clock)
    download_options: dict[str, Any] = {
        **options,
        "writethumbnail": True,
        "outtmpl": {
            "default": str(parts_dir / "%(playlist_index|0)s.%(ext)s"),
            "thumbnail": str(poster.with_suffix("")),
        },
        "merge_output_format": "mp4",
        "format": "bestvideo+bestaudio/best",
        "postprocessors": [
            {"key": "EmbedThumbnail", "already_have_thumbnail": True},
            {"key": "FFmpegMetadata", "add_chapters": True, "add_metadata": True},
        ],
        "concurrent_fragment_downloads": 1,
        "skip_unavailable_fragments": False,
        "fragment_retries": FRAGMENT_RETRIES,
        "retry_sleep_functions": {"fragment": fragment_retry_sleep},
        "progress_hooks": [progress],
    }
    if captions:
        download_options |= {
            "writesubtitles": True,
            "subtitleslangs": list(CAPTION_LANGUAGES),
            "subtitlesformat": "vtt/best",
        }
        download_options["postprocessors"] = [
            *download_options["postprocessors"],
            {"key": "FFmpegSubtitlesConvertor", "format": "srt"},
        ]
    logger.info(f"Downloading video: {url}")
    try:
        info = ytdlp.extract_info(
            url,
            download_options,
            download=True,
            # The fixup must precede the convertor in the before_dl chain.
            before_download=(
                JpegThumbnailFixupPP(),
                # The typeshed yt-dlp stubs omit `postprocessor.ffmpeg`, so its
                # classes do not type as the stubs' `PostProcessor`.
                FFmpegThumbnailsConvertorPP(format="jpg"),  # pyright: ignore[reportArgumentType]
            ),
        )
    finally:
        progress.close()
    logger.success(f"Downloaded: {info.get('title', url)}")


def download_full_video(
    ytdlp: YtDlp,
    ffmpeg: FfmpegRunner,
    url: str,
    *,
    parts_dir: Path,
    output: Path,
    poster: Path,
    options: Mapping[str, Any],
    captions: bool,
    events: EventSink,
) -> None:
    """Make sure `output` holds the whole video: download the parts
    (`download_video`), check their audio, and join them.

    An existing `output` is the finished download (fixed-name cache): the
    join writes it atomically and deletes the parts only afterwards, so a
    run interrupted after the join reuses it instead of downloading again.
    """
    if output.exists():
        logger.info(f"Reusing the downloaded video {output}")
        return
    download_video(
        ytdlp,
        url,
        parts_dir=parts_dir,
        poster=poster,
        options=options,
        captions=captions,
        events=events,
    )
    parts = downloaded_parts(parts_dir)
    if not parts:
        raise SourceError(f"yt-dlp finished without video parts in {parts_dir}")
    for part in parts:
        verify_audio(ffmpeg, part)
    join_parts(ffmpeg, parts, output)


def verify_audio(ffmpeg: FfmpegRunner, part: Path) -> None:
    """Fail on a part whose audio has gaps.

    yt-dlp treats a finished file as already downloaded, so the part has to
    be deleted before a re-run fetches it again.
    """
    gaps = find_audio_gaps(ffmpeg, part)
    if not gaps:
        return
    spans = ", ".join(f"{gap.start:.2f}s→{gap.end:.2f}s" for gap in gaps)
    raise SourceError(
        f"Downloaded {part.name} is missing audio at {spans}; "
        f"delete {part} and re-run to download it again"
    )


def downloaded_parts(parts_dir: Path) -> list[Path]:
    """The downloaded video parts (`<index>.mp4`), in playlist order.

    yt-dlp's intermediates (`0.f299.mp4`, `0.temp.mp4`) never count.
    """
    return sorted(
        path for path in parts_dir.glob("*.mp4") if _PART_NAME.fullmatch(path.name)
    )


class DownloadProgress:
    """yt-dlp progress hook: one event progress bar per downloaded file.

    yt-dlp downloads the video and audio formats as separate files, each
    with its own bar; updates are throttled to `_PROGRESS_INTERVAL_S`.
    """

    def __init__(self, events: EventSink, clock: Callable[[], float]) -> None:
        self._events = events
        self._clock = clock
        self._scope: str | None = None
        self._filename: str | None = None
        self._fraction = 0.0
        self._last_update = float("-inf")

    def __call__(self, update: dict[str, Any]) -> None:
        status = update.get("status")
        if status == "finished":
            self._finish_current()
            return
        if status != "downloading":
            return
        filename = Path(update.get("filename") or "").name
        if filename != self._filename:
            self._finish_current()
            self._start(filename)
        now = self._clock()
        if now - self._last_update < _PROGRESS_INTERVAL_S:
            return
        self._last_update = now
        total = update.get("total_bytes") or update.get("total_bytes_estimate") or 0
        if total <= 0 or self._scope is None:
            return
        fraction = min(1.0, (update.get("downloaded_bytes") or 0) / total)
        if fraction <= self._fraction:
            return
        self._events.emit(
            ProgressAdvanced(self._scope, fraction - self._fraction, _note(update))
        )
        self._fraction = fraction

    def _start(self, filename: str) -> None:
        self._filename = filename
        self._scope = f"download:{filename}"
        self._fraction = 0.0
        self._events.emit(ProgressStarted(self._scope, f"Downloading {filename}", 1.0))

    def close(self) -> None:
        """End an open bar where it stands: a failed download never reports
        its file finished."""
        if self._scope is not None:
            self._events.emit(ProgressFinished(self._scope))
        self._reset()

    def _finish_current(self) -> None:
        if self._scope is not None:
            if self._fraction < 1.0:
                self._events.emit(ProgressAdvanced(self._scope, 1.0 - self._fraction))
            self._events.emit(ProgressFinished(self._scope))
        self._reset()

    def _reset(self) -> None:
        self._scope = None
        self._filename = None
        self._fraction = 0.0


def _note(update: Mapping[str, Any]) -> str | None:
    parts: list[str] = []
    if speed := update.get("speed"):
        parts.append(f"{speed / _MIB:.1f} MiB/s")
    if eta := update.get("eta"):
        parts.append(f"eta {int(eta)}s")
    return " · ".join(parts) or None
