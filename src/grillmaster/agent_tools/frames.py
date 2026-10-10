"""`get_frames`: video stills returned to the agent as MCP image content.

Frames are also kept under the manifest's `frames_dir` for auditing; the
text item lists each timestamp with its file path, so a backend that drops
image results can still open the files itself.
"""

from __future__ import annotations

import math
from functools import cached_property
from typing import TYPE_CHECKING

from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.utilities.types import Image
from mcp.types import ImageContent, TextContent

from grillmaster.media import probe
from grillmaster.media.errors import MediaError
from grillmaster.media.frames import extract_frames

if TYPE_CHECKING:
    from grillmaster.core.tool_session import FramesTool
    from grillmaster.media.ffmpeg import FfmpegRunner

MAX_FRAMES_PER_CALL = 20


class FrameGrabber:
    """Serves `get_frames` for one session; probes the video duration once."""

    def __init__(self, config: FramesTool, runner: FfmpegRunner) -> None:
        self._config = config
        self._runner = runner

    def describe(self) -> str:
        """Tool description with this session's window and limit."""
        start, end = self._config.window
        end_text = "the end of the video (exclusive)" if end is None else f"{end:.3f}s"
        return (
            "Extract still frames from the video at the given timestamps "
            "(seconds) and return them as images. Use it to read on-screen "
            "text or check who is speaking. "
            f"Timestamps must lie between {start:.3f}s and {end_text}; "
            f"at most {MAX_FRAMES_PER_CALL} per call."
        )

    def get_frames(self, times: list[float]) -> list[TextContent | ImageContent]:
        timestamps = self._validate(times)
        try:
            paths = extract_frames(
                self._runner,
                self._config.video,
                timestamps,
                self._config.frames_dir,
                self._config.max_side,
            )
        except MediaError as error:
            raise ToolError(str(error)) from error
        listing = "\n".join(
            f"{time:.3f}s: {path}" for time, path in zip(timestamps, paths, strict=True)
        )
        header = TextContent(
            type="text",
            text=f"{len(paths)} frame(s), images follow in this order:\n{listing}",
        )
        return [header, *(Image(path=path).to_image_content() for path in paths)]

    def _validate(self, times: list[float]) -> list[float]:
        """Sorted, millisecond-unique timestamps; raises `ToolError` otherwise."""
        if not times:
            raise ToolError("times is empty; pass at least one timestamp")
        if len(times) > MAX_FRAMES_PER_CALL:
            raise ToolError(
                f"{len(times)} timestamps requested; "
                f"at most {MAX_FRAMES_PER_CALL} per call"
            )
        start, end = self._config.window
        length = self._video_duration
        stop = length if end is None else min(end, length)

        # Frame files are named at millisecond precision, so the rounded value
        # is what ffmpeg seeks to. The last frame starts before `length`: a
        # seek to the very end extracts nothing.
        def allowed(t: float) -> bool:
            seek = round(t, 3)
            return math.isfinite(t) and start <= seek <= stop and seek < length

        outside = [t for t in times if not allowed(t)]
        if outside:
            listed = ", ".join(f"{t:g}" for t in outside)
            raise ToolError(
                f"timestamps outside the allowed window "
                f"{start:.3f}s-{stop:.3f}s: {listed}"
            )
        return sorted({round(t, 3) for t in times})

    @cached_property
    def _video_duration(self) -> float:
        """Probed on first use; a failed probe is retried on the next call."""
        try:
            return probe.duration(self._runner, self._config.video)
        except MediaError as error:
            raise ToolError(str(error)) from error
