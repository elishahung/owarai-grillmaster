"""Download a YouTube live-chat replay.

yt-dlp exposes the replay as a pseudo-subtitle track named `live_chat`: a
JSON-lines file of `replayChatItemAction` records, each stamped with
`videoOffsetTimeMsec` on the full video's timeline. Normalizing it is
`live_chat.parse`'s job.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from grillmaster.sources.errors import SourceError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from grillmaster.sources.ytdlp import YtDlp

LIVE_CHAT_TRACK = "live_chat"
_DOWNLOAD_STEM = "download"


def download_live_chat(
    ytdlp: YtDlp, url: str, output: Path, *, options: Mapping[str, Any]
) -> None:
    """Write the replay JSON lines for `url` to `output`.

    An existing `output` is reused (fixed-filename cache). Raises
    `SourceError` when the video has no replay, since `--chat` asked for one.
    """
    if output.exists():
        logger.info(f"Live chat already downloaded: {output}")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    stem = output.parent / _DOWNLOAD_STEM
    # yt-dlp appends `.<track>.<ext>` to the output template.
    written = stem.with_name(f"{stem.name}.{LIVE_CHAT_TRACK}.json")
    chat_options = {
        **options,
        "skip_download": True,
        "writesubtitles": True,
        "subtitleslangs": [LIVE_CHAT_TRACK],
        "outtmpl": {"default": str(stem)},
    }
    logger.info(f"Downloading live chat replay: {url}")
    ytdlp.extract_info(url, chat_options, download=True)
    if not written.exists():
        raise SourceError(
            f"No live chat replay available for {url}; "
            "drop --chat for videos that were not streamed live"
        )
    written.replace(output)
    logger.success(f"Live chat replay saved: {output}")
