"""Download a YouTube live-chat replay.

yt-dlp exposes the replay as a pseudo-subtitle track named ``live_chat``: a
JSON-lines file of ``replayChatItemAction`` records, each stamped with
``videoOffsetTimeMsec`` on the full video's timeline.
"""

from pathlib import Path

from loguru import logger

from .client import get_ytdlp_client_for_url

LIVE_CHAT_TRACK = "live_chat"


def download_live_chat(url: str, output_path: Path) -> None:
    """Write the replay JSON lines for ``url`` to ``output_path``.

    An existing file is reused (fixed-filename cache; delete it to
    re-download). Raises when the video has no chat replay, since the caller
    asked for one explicitly.
    """
    if output_path.exists():
        logger.info(f"Live chat already downloaded: {output_path}")
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    stem = output_path.parent / "download"
    # yt-dlp appends `.<track>.<ext>` to the output template.
    written = stem.with_name(f"{stem.name}.{LIVE_CHAT_TRACK}.json")
    opts = {
        "skip_download": True,
        "writesubtitles": True,
        "subtitleslangs": [LIVE_CHAT_TRACK],
        "outtmpl": {"default": str(stem)},
        # Routed through the loguru client so nothing reaches stdout under
        # the dashboard; progress is off because each fragment redraw would
        # otherwise become a log line.
        "noprogress": True,
    }
    logger.info(f"Downloading live chat replay: {url}")
    with get_ytdlp_client_for_url(url, opts) as ydl:
        ydl.extract_info(url, download=True)

    if not written.exists():
        raise ValueError(
            f"No live chat replay available for {url}; "
            "drop --chat for videos that were not streamed live"
        )
    written.replace(output_path)
    logger.success(f"Live chat replay saved: {output_path}")
