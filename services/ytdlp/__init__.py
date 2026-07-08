"""yt-dlp service for downloading videos and extracting metadata.

This module provides a unified interface for downloading videos from various
sources using yt-dlp, with integrated logging and metadata extraction.
"""

from .broadcast_date import resolve_broadcast_date
from .download import download_video, parse_section_time
from .info import (
    get_abema_episode_talents,
    get_tver_episode_talents,
    get_video_info,
)
from .subtitles import normalize_official_subtitle

__all__ = [
    "download_video",
    "parse_section_time",
    "normalize_official_subtitle",
    "get_abema_episode_talents",
    "get_tver_episode_talents",
    "get_video_info",
    "resolve_broadcast_date",
]
