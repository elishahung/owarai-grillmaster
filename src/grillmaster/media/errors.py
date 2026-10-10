"""Media failures."""

from __future__ import annotations


class MediaError(Exception):
    """An ffmpeg/ffprobe run failed or produced unusable output."""
