"""`listen`: a fresh transcription of an audio span, returned as text.

A text-only agent (the proofread on claude or codex) cannot hear the show,
so an ASR-suspect line it would fix stays unresolved. This tool cuts the
span from the project's speech audio and runs it, as one agent session of
its own (role `listen`, an audio-hearing backend), through a plain
transcription prompt; the transcript is a second hearing next to `ja.srt`.

The tool server is a separate process, so it builds its own `AgentRunner`
for that one role; its CLI children stay inside the calling agent's process
tree, so an abort still kills them. A span's clip, transcript and session
records live in `<clips_dir>/<start>-<end>/`; an existing transcript is
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
from grillmaster.agents.errors import AgentError
from grillmaster.agents.runner import AgentRunner
from grillmaster.agents.task import AgentTask, TextOutput
from grillmaster.core.fs import atomic_write_text
from grillmaster.core.model_spec import Role
from grillmaster.core.prompts import load_prompt
from grillmaster.core.timecode import TimeRange
from grillmaster.events.bus import EventBus
from grillmaster.media.audio import slice_audio
from grillmaster.media.errors import MediaError

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.agents.task import AgentResult
    from grillmaster.core.tool_session import ListenTool
    from grillmaster.media.ffmpeg import FfmpegRunner

# Transcriptions one tool server runs at once.
MAX_CONCURRENT_LISTENS = 4
TRANSCRIPT_FILE = "transcript.txt"
CLIP_FILE = "clip.ogg"


class ListenAgents(Protocol):
    """The part of `AgentRunner` the tool uses."""

    def run[T](self, task: AgentTask[T]) -> AgentResult[T]: ...


def build_agents(config: ListenTool) -> ListenAgents:
    """An `AgentRunner` that knows only the `listen` role. Its events go
    nowhere (the session records keep the trace), and its slots are its own:
    the calling grill process's `max_concurrent` does not reach this one."""
    return AgentRunner(
        {Role.LISTEN: config.model},
        AdapterRegistry(),
        max_concurrent=MAX_CONCURRENT_LISTENS,
        timeout_s=config.timeout_s,
        events=EventBus(),
    )


def span_name(span: TimeRange) -> str:
    """The span's directory name under `clips_dir`, e.g. `0812.000-0845.500`."""
    return f"{span.start:08.3f}-{span.end:08.3f}"


class Listener:
    """Serves `listen` for one session; probes the audio duration once."""

    def __init__(
        self, config: ListenTool, runner: FfmpegRunner, agents: ListenAgents
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
            "Transcribe the show's audio between `start` and `end` (seconds) "
            "afresh with a speech-capable model and return the Japanese "
            "transcript as plain text, one utterance per line. It is a second "
            "hearing, independent of ja.srt; it takes tens of seconds. "
            f"The span must lie inside the audio and last at most "
            f"{self._config.max_span_s:g}s."
        )

    async def listen(self, start: float, end: float) -> str:
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
            clip = clip_dir / CLIP_FILE
            try:
                slice_audio(self._runner, self._config.audio, clip, span)
                text = self._agents.run(_task(span, clip, clip_dir)).output.strip()
            except MediaError as error:
                raise ToolError(str(error)) from error
            except AgentError as error:
                raise ToolError(f"transcription failed: {error}") from error
            atomic_write_text(transcript, text + "\n")
        return f"Transcript of {span.start:.3f}s-{span.end:.3f}s:\n{text}"

    def _validate(self, start: float, end: float) -> TimeRange:
        stop = self._audio_duration
        if not (math.isfinite(start) and math.isfinite(end)):
            raise ToolError(f"start and end must be numbers: {start:g}-{end:g}")
        # Clip names and the cut are at millisecond precision.
        start, end = round(start, 3), round(end, 3)
        if end <= start:
            raise ToolError(f"end must be after start: {start:g}-{end:g}")
        if start < 0 or end > stop:
            raise ToolError(
                f"span {start:g}-{end:g} lies outside the audio (0-{stop:.3f}s)"
            )
        if end - start > self._config.max_span_s:
            raise ToolError(
                f"span {start:g}-{end:g} lasts {end - start:g}s; "
                f"at most {self._config.max_span_s:g}s per call"
            )
        return TimeRange(start, end)

    @cached_property
    def _audio_duration(self) -> float:
        """Probed on first use; a failed probe is retried on the next call."""
        return media_duration(self._runner, self._config.audio)


def _task(span: TimeRange, clip: Path, clip_dir: Path) -> AgentTask[str]:
    return AgentTask(
        name=f"listen/{span_name(span)}",
        role=Role.LISTEN,
        instructions=load_prompt(__package__, "listen.md"),
        prompt=f"The clip is {span.duration:.1f} seconds long.",
        session_dir=clip_dir / "session",
        workdir=clip_dir,
        output=TextOutput(),
        audio=(clip,),
        max_repairs=1,
    )
