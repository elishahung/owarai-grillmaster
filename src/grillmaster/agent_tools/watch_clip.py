"""`watch_clip`: what a span of the show says and shows, as Meta's Muse reads it.

A text-only agent (the proofread on claude or codex) cannot watch the show,
so an ASR-suspect line it would fix stays unresolved. This tool cuts the
span from `video.mp4` as a small preview clip (picture and sound) and asks
Muse (`meta.MetaVideoModel`, one API request, no agent session) through
`watch_clip.md` for three sections: the speech with approximate times and
speakers, the on-screen captions and name cards verbatim, and a short scene
description. Muse's times count from the clip's start; the tool shifts each
`[t]` line prefix to the video's time so the agent can match `ja.srt`.

Measured on Muse 1.3 (2026-10-21): the captions and name cards are read
reliably and spell the names (西川きよし(80), 石塚英彦(64)); the speech
drops and occasionally invents lines (at `medium`, mostly echoing the
captions), and times are within about 2 s.

A span's clip and records (`watch_record`: the answer with its model,
effort, usage and USD cost, plus any request billed without an answer) live
in `<clips_dir>/<start>-<end>/`; an existing answer is returned again
without a new request, and the proofread stage adds the cost of each record
new since its start to the project's spend (`watch_record.cost_since`). The handler is async and runs the request on a worker
thread, so parallel calls overlap, at most `MAX_CONCURRENT_REQUESTS` at once.
"""

from __future__ import annotations

import math
import re
import threading
from functools import cached_property
from typing import TYPE_CHECKING

import anyio.to_thread
from mcp.server.fastmcp.exceptions import ToolError

from grillmaster.agent_tools._media import media_duration
from grillmaster.agent_tools.meta import (
    PRICES,
    MetaError,
    MetaVideoModel,
    VideoModel,
    read_api_key,
)
from grillmaster.agent_tools.watch_record import (
    RECORD_FILE,
    FailedWatch,
    WatchRecord,
    failed_path,
)
from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.core.prompts import load_prompt
from grillmaster.core.timecode import TimeRange
from grillmaster.core.tool_session import WATCH_CLIP_FILE
from grillmaster.media.errors import MediaError
from grillmaster.media.video import slice_preview

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.agent_tools.meta import Usage
    from grillmaster.core.tool_session import WatchClipTool
    from grillmaster.media.ffmpeg import FfmpegRunner

# Requests one tool server sends at once (the contributor key allows 150 RPM).
MAX_CONCURRENT_REQUESTS = 4
# A time at the start of an answer line, in clip time: `[12.3]` as asked,
# or the `[12.3s]`, `[0:12.3]` and `- [12.3]` forms a model may slip into.
_LINE_TIME = re.compile(
    r"^(?P<lead>[ \t]*(?:[-*・][ \t]*)?)\[(?:(?P<minutes>\d+):)?(?P<seconds>\d+(?:\.\d+)?)s?\]",
    re.MULTILINE,
)


def build_model(config: WatchClipTool) -> VideoModel:
    """The configured Muse model; raises `MetaError` without a key."""
    return MetaVideoModel(
        config.model, config.effort, read_api_key(config.env_file), config.timeout_s
    )


def span_name(span: TimeRange) -> str:
    """The span's directory name under `clips_dir`, e.g. `0812.000-0845.500`."""
    return f"{span.start:08.3f}-{span.end:08.3f}"


def shift_times(text: str, offset_s: float) -> str:
    """Each line's leading time moved from clip time to video time, as `[t]`."""

    def shift(match: re.Match[str]) -> str:
        seconds = int(match["minutes"] or 0) * 60 + float(match["seconds"])
        return f"{match['lead']}[{seconds + offset_s:.1f}]"

    return _LINE_TIME.sub(shift, text)


class ClipWatcher:
    """Serves `watch_clip` for one session; probes the video duration once."""

    def __init__(
        self, config: WatchClipTool, runner: FfmpegRunner, model: VideoModel
    ) -> None:
        self._config = config
        self._runner = runner
        self._model = model
        self._requests = threading.Semaphore(MAX_CONCURRENT_REQUESTS)
        # One lock per span: a repeated span waits for the first record
        # instead of cutting and requesting into the same directory.
        self._span_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def describe(self) -> str:
        """Tool description with this session's span limit."""
        return (
            "Watch the show between `start` and `end` (seconds): a separate "
            "model watches that clip, picture and sound, and returns three "
            "sections in Japanese: 発話 (the speech, one utterance per line "
            "with its approximate start time in video seconds and the speaker "
            "when visible), テロップ (every on-screen caption, name card and "
            "label, verbatim, with its time) and 場面 (a short scene "
            "description). It is independent of ja.srt and takes tens of "
            f"seconds. The span must lie inside the video and last at most "
            f"{self._config.max_span_s:g}s."
        )

    async def call(self, start: float, end: float) -> str:
        return await anyio.to_thread.run_sync(self.watch, start, end)

    def watch(self, start: float, end: float) -> str:
        span = self._validate(start, end)
        name = span_name(span)
        with self._locks_guard:
            lock = self._span_locks.setdefault(name, threading.Lock())
        with lock:
            record = self._record(span, self._config.clips_dir / name)
        return (
            f"Clip {span.start:.3f}s-{span.end:.3f}s "
            f"(times are video seconds, within about 2 s):\n{record.text}"
        )

    def _record(self, span: TimeRange, clip_dir: Path) -> WatchRecord:
        path = clip_dir / RECORD_FILE
        if path.is_file():
            return read_model(path, WatchRecord)
        clip = clip_dir / WATCH_CLIP_FILE
        try:
            slice_preview(self._runner, self._config.video, clip, span)
            with self._requests:
                answer = self._model.ask(
                    load_prompt(__package__, "watch_clip.md"), clip
                )
        except MediaError as error:
            raise ToolError(str(error)) from error
        except MetaError as error:
            if error.usage is not None:
                failed = FailedWatch(
                    model=self._config.model,
                    effort=self._config.effort,
                    usage=error.usage,
                    cost_usd=self._cost(error.usage),
                    error=str(error),
                )
                write_model(failed_path(clip_dir), failed)
            raise ToolError(f"watching the clip failed: {error}") from error
        record = WatchRecord(
            model=self._config.model,
            effort=self._config.effort,
            usage=answer.usage,
            cost_usd=self._cost(answer.usage),
            text=shift_times(answer.text, span.start),
        )
        write_model(path, record)
        return record

    def _cost(self, usage: Usage) -> float:
        return usage.cost_usd(PRICES[self._config.model])

    def _validate(self, start: float, end: float) -> TimeRange:
        stop = self._video_duration
        if not (math.isfinite(start) and math.isfinite(end)):
            raise ToolError(f"start and end must be numbers: {start:g}-{end:g}")
        # Clip names and the cut are at millisecond precision.
        start, end = round(start, 3), round(end, 3)
        if end <= start:
            raise ToolError(f"end must be after start: {start:g}-{end:g}")
        if start < 0 or end > stop:
            raise ToolError(
                f"span {start:g}-{end:g} lies outside the video (0-{stop:.3f}s)"
            )
        if end - start > self._config.max_span_s:
            raise ToolError(
                f"span {start:g}-{end:g} lasts {end - start:g}s; "
                f"at most {self._config.max_span_s:g}s per call"
            )
        return TimeRange(start, end)

    @cached_property
    def _video_duration(self) -> float:
        """Probed on first use; a failed probe is retried on the next call."""
        return media_duration(self._runner, self._config.video)
