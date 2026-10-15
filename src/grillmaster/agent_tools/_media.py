"""Media helpers the tools share."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.fastmcp.exceptions import ToolError

from grillmaster.media import probe
from grillmaster.media.errors import MediaError

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.media.ffmpeg import FfmpegRunner


def media_duration(runner: FfmpegRunner, path: Path) -> float:
    """`path`'s duration in seconds; a failed probe is a `ToolError`."""
    try:
        return probe.duration(runner, path)
    except MediaError as error:
        raise ToolError(str(error)) from error
