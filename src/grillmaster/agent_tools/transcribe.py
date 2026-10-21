"""`transcribe`: a fresh transcription of a span of the show, returned as text.

A text-only agent (the proofread on claude or codex) cannot hear the show,
so an ASR-suspect line it would fix stays unresolved. This tool cuts the
span from `video.mp4` as a small preview clip (picture and sound) and runs
it, as one agent session of its own (role `transcribe`, a video-watching
backend), through a plain transcription prompt; the transcript is a second
hearing next to `ja.srt`. The picture is why it is a video: captions and
name cards let the model spell the names the sound alone leaves ambiguous
(measured against audio-only clips: 今井らいぱち, 庄司智春, 松浦 were
written right only from the video).

The tool server is a separate process, so it builds its own `AgentRunner`
for that one role; its CLI children stay inside the calling agent's process
tree, so an abort still kills them. A span's clip, transcript and session
records live in `<clips_dir>/<start>-<end>/` (the session's cwd is its empty
`work/`, and it gets no built-in tools: `ToolAccess.NONE`); an existing transcript is
returned again without a new session. The handler is async and runs the
session on a worker thread, so parallel calls transcribe concurrently.
"""

from __future__ import annotations

import math
import threading
from functools import cached_property
from typing import TYPE_CHECKING, Protocol

import anyio.to_thread
from mcp.server.fastmcp.exceptions import ToolError

from grillmaster.agent_tools._media import media_duration
from grillmaster.agents.adapters import AdapterRegistry
from grillmaster.agents.adapters.base import Media, MediaKind
from grillmaster.agents.errors import AgentError
from grillmaster.agents.runner import AgentRunner
from grillmaster.agents.task import AgentTask, TextOutput
from grillmaster.core.fs import atomic_write_text
from grillmaster.core.model_spec import Role
from grillmaster.core.prompts import load_prompt
from grillmaster.core.timecode import TimeRange
from grillmaster.core.tool_session import TRANSCRIBE_CLIP_FILE
from grillmaster.events.bus import EventBus
from grillmaster.media.errors import MediaError
from grillmaster.media.video import slice_preview

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.agents.task import AgentResult
    from grillmaster.core.tool_session import TranscribeTool
    from grillmaster.media.ffmpeg import FfmpegRunner

# Transcriptions one tool server runs at once.
MAX_CONCURRENT_TRANSCRIPTIONS = 4
# How long a call keeps retrying a server out of capacity: shorter than the
# runner's default, since the calling proofread does without a transcript
# sooner than it waits (a stalled gemini refuses a turn every ~80 s).
UNAVAILABLE_BUDGET_S = 120.0
TRANSCRIPT_FILE = "transcript.txt"


class TranscribeAgents(Protocol):
    """The part of `AgentRunner` the tool uses."""

    def run[T](self, task: AgentTask[T]) -> AgentResult[T]: ...


def build_agents(config: TranscribeTool) -> TranscribeAgents:
    """An `AgentRunner` that knows only the `transcribe` role. Its events go
    nowhere (the session records keep the trace), and its slots are its own:
    the calling grill process's `max_concurrent` does not reach this one."""
    return AgentRunner(
        {Role.TRANSCRIBE: config.model},
        AdapterRegistry(),
        max_concurrent=MAX_CONCURRENT_TRANSCRIPTIONS,
        timeout_s=config.timeout_s,
        unavailable_budget_s=UNAVAILABLE_BUDGET_S,
        events=EventBus(),
    )


def span_name(span: TimeRange) -> str:
    """The span's directory name under `clips_dir`, e.g. `0812.000-0845.500`."""
    return f"{span.start:08.3f}-{span.end:08.3f}"


class Transcriber:
    """Serves `transcribe` for one session; probes the video duration once."""

    def __init__(
        self, config: TranscribeTool, runner: FfmpegRunner, agents: TranscribeAgents
    ) -> None:
        self._config = config
        self._runner = runner
        self._agents = agents
        # One lock per span: a repeated span waits for the first transcript
        # instead of cutting and transcribing into the same directory.
        self._span_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def describe(self) -> str:
        """Tool description with this session's span limit."""
        return (
            "Transcribe the speech of the show between `start` and `end` "
            "(seconds) afresh: a separate model watches and hears that clip "
            "(its captions and name cards help it spell names) and returns "
            "the Japanese transcript as plain text, one utterance per line. "
            "It is a second hearing, independent of ja.srt; it takes tens of "
            f"seconds. The span must lie inside the video and last at most "
            f"{self._config.max_span_s:g}s."
        )

    async def call(self, start: float, end: float) -> str:
        return await anyio.to_thread.run_sync(self.transcribe, start, end)

    def transcribe(self, start: float, end: float) -> str:
        span = self._validate(start, end)
        name = span_name(span)
        with self._locks_guard:
            lock = self._span_locks.setdefault(name, threading.Lock())
        with lock:
            return self._transcribe(span, self._config.clips_dir / name)

    def _transcribe(self, span: TimeRange, clip_dir: Path) -> str:
        transcript = clip_dir / TRANSCRIPT_FILE
        if transcript.is_file():
            text = transcript.read_text(encoding="utf-8").strip()
        else:
            clip = clip_dir / TRANSCRIBE_CLIP_FILE
            try:
                slice_preview(self._runner, self._config.video, clip, span)
                text = self._agents.run(_task(span, clip, clip_dir)).output.strip()
            except MediaError as error:
                raise ToolError(str(error)) from error
            except AgentError as error:
                raise ToolError(f"transcription failed: {error}") from error
            atomic_write_text(transcript, text + "\n")
        return f"Transcript of {span.start:.3f}s-{span.end:.3f}s:\n{text}"

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


def _task(span: TimeRange, clip: Path, clip_dir: Path) -> AgentTask[str]:
    return AgentTask(
        name=f"transcribe/{span_name(span)}",
        role=Role.TRANSCRIBE,
        instructions=load_prompt(__package__, "transcribe.md"),
        prompt=f"The clip is {span.duration:.1f} seconds long.",
        session_dir=clip_dir / "session",
        # An empty cwd: the clip goes inline, and a model that finds it as a
        # file processes that instead of watching it.
        workdir=clip_dir / "work",
        output=TextOutput(),
        media=(Media(MediaKind.VIDEO, clip),),
        # The answer is the clip watched; a tool would only process it.
        builtin_tools=False,
        max_repairs=1,
    )
