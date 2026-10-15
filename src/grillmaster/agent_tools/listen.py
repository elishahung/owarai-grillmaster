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
from functools import cached_property
from typing import TYPE_CHECKING, Protocol

import anyio.to_thread
from mcp.server.fastmcp.exceptions import ToolError

from grillmaster.agents.adapters import AdapterRegistry
from grillmaster.agents.errors import AgentError
from grillmaster.agents.runner import AgentRunner
from grillmaster.agents.task import AgentTask, TextOutput
from grillmaster.core.fs import atomic_write_text
from grillmaster.core.model_spec import ModelSpec, Role
from grillmaster.core.prompts import load_prompt
from grillmaster.core.timecode import TimeRange
from grillmaster.media import probe
from grillmaster.media.audio import extract_audio_segment
from grillmaster.media.errors import MediaError

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.agents.task import AgentResult
    from grillmaster.core.tool_session import ListenTool
    from grillmaster.events.types import Event
    from grillmaster.media.ffmpeg import FfmpegRunner

# Transcriptions one tool server runs at once.
MAX_CONCURRENT_LISTENS = 4
TRANSCRIPT_FILE = "transcript.txt"
CLIP_FILE = "clip.ogg"


class ListenAgents(Protocol):
    """The part of `AgentRunner` the tool uses."""

    def run[T](self, task: AgentTask[T]) -> AgentResult[T]: ...


class _DropEvents:
    """The tool server has no event bus; the session records keep the trace."""

    def emit(self, event: Event) -> None:
        pass


def build_agents(config: ListenTool) -> ListenAgents:
    """An `AgentRunner` that knows only the `listen` role."""
    return AgentRunner(
        {Role.LISTEN: ModelSpec.parse(config.model)},
        AdapterRegistry(),
        max_concurrent=MAX_CONCURRENT_LISTENS,
        timeout_s=config.timeout_s,
        events=_DropEvents(),
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

    def describe(self) -> str:
        """Tool description with this session's window and span limit."""
        start, end = self._config.window
        end_text = "the end of the audio" if end is None else f"{end:.3f}s"
        return (
            "Transcribe the show's audio between `start` and `end` (seconds) "
            "afresh with a speech-capable model and return the Japanese "
            "transcript as plain text, one utterance per line. It is a second "
            "hearing, independent of ja.srt; it takes tens of seconds. "
            f"The span must lie between {start:.3f}s and {end_text} and last "
            f"at most {self._config.max_span_s:g}s."
        )

    async def listen(self, start: float, end: float) -> str:
        return await anyio.to_thread.run_sync(self.transcribe, start, end)

    def transcribe(self, start: float, end: float) -> str:
        span = self._validate(start, end)
        clip_dir = self._config.clips_dir / span_name(span)
        transcript = clip_dir / TRANSCRIPT_FILE
        if not transcript.is_file():
            clip = clip_dir / CLIP_FILE
            try:
                if not clip.is_file():
                    extract_audio_segment(self._runner, self._config.audio, clip, span)
            except MediaError as error:
                raise ToolError(str(error)) from error
            try:
                result = self._agents.run(_task(span, clip, clip_dir))
            except AgentError as error:
                raise ToolError(f"transcription failed: {error}") from error
            atomic_write_text(transcript, result.output.strip() + "\n")
        text = transcript.read_text(encoding="utf-8").strip()
        return f"Transcript of {span.start:.3f}s-{span.end:.3f}s:\n{text}"

    def _validate(self, start: float, end: float) -> TimeRange:
        low, high = self._config.window
        stop = self._audio_duration if high is None else min(high, self._audio_duration)
        if not (math.isfinite(start) and math.isfinite(end)) or end <= start:
            raise ToolError(f"end must be after start: {start:g}-{end:g}")
        if start < low or end > stop:
            raise ToolError(
                f"span {start:g}-{end:g} lies outside the allowed window "
                f"{low:.3f}s-{stop:.3f}s"
            )
        if end - start > self._config.max_span_s:
            raise ToolError(
                f"span {start:g}-{end:g} lasts {end - start:g}s; "
                f"at most {self._config.max_span_s:g}s per call"
            )
        return TimeRange(round(start, 3), round(end, 3))

    @cached_property
    def _audio_duration(self) -> float:
        """Probed on first use; a failed probe is retried on the next call."""
        try:
            return probe.duration(self._runner, self._config.audio)
        except MediaError as error:
            raise ToolError(str(error)) from error


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
