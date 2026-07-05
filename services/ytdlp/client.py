"""yt-dlp client configuration and logging adapter.

This module provides a configured yt-dlp client with loguru integration
for consistent logging across the application.
"""

import yt_dlp
from loguru import logger
from typing import Any, cast
from settings import settings


class YtDlpLoguruAdapter:
    """Adapter that redirects yt-dlp's standard logging to loguru.

    yt-dlp expects a logger object with debug, info, warning, and error methods.
    This adapter maps those calls to loguru with a [yt-dlp] prefix for easy filtering.
    """

    def debug(self, msg: str):
        # yt-dlp uses 'debug' for a lot of verbose info.
        # We can map it to loguru's debug.
        if not msg.startswith("[debug] "):
            logger.debug(f"[yt-dlp] {msg}")

    def info(self, msg: str):
        # yt-dlp uses 'info' for standard output (e.g., download progress).
        # Mapping to info helps track progress.
        logger.info(f"[yt-dlp] {msg}")

    def warning(self, msg: str):
        logger.warning(f"[yt-dlp] {msg}")

    def error(self, msg: str):
        logger.error(f"[yt-dlp] {msg}")


cookies_txt_path = (
    str(settings.cookies_txt_path.absolute())
    if settings.cookies_txt_path
    else None
)
if cookies_txt_path:
    logger.debug(f"Using cookies from: {cookies_txt_path}")


_BILIBILI_PLAYURL_FALLBACK_URL = "https://api.bilibili.com/x/player/playurl"
_bilibili_412_patch_applied = False


def _is_bilibili_input(input_str: str) -> bool:
    """Return True for BiliBili URLs and common direct BV identifiers."""
    return "bilibili.com" in input_str.lower() or input_str.startswith("BV")


def _apply_bilibili_412_playurl_patch() -> None:
    """Temporarily route BiliBili playinfo requests around yt-dlp's 412-prone endpoint.

    yt-dlp 2026.06.09 calls `/x/player/wbi/playurl` for BiliBili playinfo. In
    July 2026 that endpoint can return HTTP 412 for videos that still work via
    `/x/player/playurl`. Keep this monkey patch narrow so it is easy to remove
    after yt-dlp ships an upstream fix.
    """
    global _bilibili_412_patch_applied
    if _bilibili_412_patch_applied:
        return

    from yt_dlp.extractor.bilibili import BiliBiliIE

    current_method = BiliBiliIE._download_playinfo
    if getattr(current_method, "__grillmaster_bilibili_412_patch__", False):
        _bilibili_412_patch_applied = True
        return

    def _download_playinfo_without_wbi_endpoint(
        self, bvid, cid, headers=None, query=None
    ):
        params = {"bvid": bvid, "cid": cid, "fnval": 4048, **(query or {})}
        if self.is_logged_in:
            params.pop("try_look", None)
        if qn := params.get("qn"):
            note = f"Downloading video format {qn} for cid {cid}"
        else:
            note = f"Downloading video formats for cid {cid}"

        return self._download_json(
            _BILIBILI_PLAYURL_FALLBACK_URL,
            bvid,
            query=self._sign_wbi(params, bvid),
            headers=headers,
            note=note,
        )["data"]

    _download_playinfo_without_wbi_endpoint.__grillmaster_bilibili_412_patch__ = True
    BiliBiliIE._download_playinfo = _download_playinfo_without_wbi_endpoint
    _bilibili_412_patch_applied = True
    logger.debug("Applied temporary BiliBili yt-dlp HTTP 412 playurl patch")


def _build_ytdlp_options(
    input_str: str | None,
    opts: dict | None,
    *,
    use_loguru_logger: bool,
) -> dict:
    """Merge shared yt-dlp defaults with source-specific options.

    Keep option selection separate from logging policy: metadata extraction uses
    the loguru adapter for consistent app logs, while downloads should keep
    yt-dlp's native progress renderer. With a custom logger, yt-dlp sends every
    progress redraw through `debug()`, which floods the terminal.
    """
    _apply_bilibili_412_playurl_patch()

    opts = {} if opts is None else opts.copy()
    ydl_opts: dict[str, Any] = {"cookiefile": cookies_txt_path}
    if use_loguru_logger:
        ydl_opts["logger"] = YtDlpLoguruAdapter()

    if (
        input_str is not None
        and _is_bilibili_input(input_str)
        and "cookiefile" not in opts
    ):
        ydl_opts["cookiefile"] = None

    ydl_opts.update(opts)
    return ydl_opts


def get_ytdlp_client(opts: dict | None = None):
    """Create a configured yt-dlp client instance.

    Creates a YoutubeDL instance with loguru logging integration and
    optional cookie authentication from settings.

    Args:
        opts: Additional yt-dlp options to merge with defaults.

    Returns:
        A configured yt_dlp.YoutubeDL instance.
    """
    ydl_opts = _build_ytdlp_options(
        input_str=None,
        opts=opts,
        use_loguru_logger=True,
    )

    return yt_dlp.YoutubeDL(cast(Any, ydl_opts))


def get_ytdlp_client_for_url(input_str: str, opts: dict | None = None):
    """Create a yt-dlp client with source-specific defaults.

    BiliBili is intentionally anonymous by default here. Some account cookies
    return a restricted format list for ordinary videos (observed on
    BV16D4y1H7Wk: cookie -> max 480p, anonymous -> 1080p). Other sources still
    inherit the configured cookies because TVer/ABEMA may need them.
    """
    ydl_opts = _build_ytdlp_options(
        input_str=input_str,
        opts=opts,
        use_loguru_logger=True,
    )

    return yt_dlp.YoutubeDL(cast(Any, ydl_opts))


def get_ytdlp_download_options_for_url(
    input_str: str, opts: dict | None = None
) -> dict:
    """Build yt-dlp options for real downloads without a custom logger."""
    return _build_ytdlp_options(
        input_str=input_str,
        opts=opts,
        use_loguru_logger=False,
    )
