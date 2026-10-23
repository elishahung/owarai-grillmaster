"""`watch_clip`: what a span of the show says and shows.

A text-only agent (the proofread on claude or codex) cannot watch the show,
so an ASR-suspect line it would fix stays unresolved. This tool cuts the
span from `video.mp4` as a small preview clip (picture and sound) and as
FLAC audio, then asks two services, each once, no agent session:

- 発話 from Azure's MAI-Transcribe-2 (`mai.MaiSpeechModel`): verbatim,
  diarized phrases, biased toward the briefing's cast names. It keeps the speech
  and its times; names and homophones may carry the wrong kanji.
- テロップ and 場面 from Meta's Muse (`meta.MetaVideoModel`, `watch_clip.md`):
  every caption and name card verbatim, and a short scene description. Its
  captions and their times are reliable; its own speech was not (it
  dropped most lines and copied captions), and gemini, tried in its place,
  heard well but timed its captions tens of seconds off.

Both answers count from the clip's start; the tool shifts their times to the
video's so the agent can match `ja.srt`. A span's clip, audio and records
(`watch_record`: `speech.json`, `watch.json`, plus any Muse request billed
without an answer) live in `<clips_dir>/<start>-<end>/`; an existing record
is reused without a new request, and the proofread stage adds the cost of
each record new since its start to the project's spend
(`watch_record.cost_since`). Each section stands on its own: a failed
request leaves its section naming the failure, the other section still
answers, and only the failed one is asked again next time. The handler is
async and runs the requests on a worker thread, so parallel calls overlap,
at most `MAX_CONCURRENT_REQUESTS` Muse requests at once.
"""

from __future__ import annotations

import math
import re
import threading
from functools import cached_property
from typing import TYPE_CHECKING

import anyio.to_thread
from mcp.server.fastmcp.exceptions import ToolError

from grillmaster.agent_tools import mai
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
    SPEECH_FILE,
    FailedWatch,
    SpeechRecord,
    WatchRecord,
    failed_path,
)
from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.core.prompts import load_prompt
from grillmaster.core.timecode import TimeRange
from grillmaster.core.tool_session import WATCH_AUDIO_FILE, WATCH_CLIP_FILE
from grillmaster.media.audio import slice_audio
from grillmaster.media.errors import MediaError
from grillmaster.media.video import slice_preview

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.agent_tools.meta import Usage
    from grillmaster.core.tool_session import WatchClipTool
    from grillmaster.media.ffmpeg import FfmpegRunner

# Muse requests one tool server sends at once (the contributor key allows 150
# RPM); the speech requests are seconds long and run unthrottled.
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


def build_speech(config: WatchClipTool) -> mai.SpeechModel:
    """MAI-Transcribe-2; raises `MaiError` without the Azure key or endpoint."""
    return mai.MaiSpeechModel(mai.read_credentials(config.env_file), config.timeout_s)


def span_name(span: TimeRange) -> str:
    """The span's directory name under `clips_dir`, e.g. `0812.000-0845.500`."""
    return f"{span.start:08.3f}-{span.end:08.3f}"


def shift_times(text: str, offset_s: float) -> str:
    """Each line's leading time moved from clip time to video time, as `[t]`."""

    def shift(match: re.Match[str]) -> str:
        seconds = int(match["minutes"] or 0) * 60 + float(match["seconds"])
        return f"{match['lead']}[{seconds + offset_s:.1f}]"

    return _LINE_TIME.sub(shift, text)


def render_speech(record: SpeechRecord) -> str:
    """The `## 発話` section: `[t] 話者N: text` per phrase."""
    lines = [
        f"[{line.start_s:.1f}] "
        + (f"話者{line.speaker}: " if line.speaker is not None else "")
        + line.text
        for line in record.phrases
    ]
    return "## 発話\n\n" + ("\n".join(lines) or "（発話なし）")


class ClipWatcher:
    """Serves `watch_clip` for one session; probes the video duration once."""

    def __init__(
        self,
        config: WatchClipTool,
        runner: FfmpegRunner,
        model: VideoModel,
        speech: mai.SpeechModel,
    ) -> None:
        self._config = config
        self._runner = runner
        self._model = model
        self._speech = speech
        self._requests = threading.Semaphore(MAX_CONCURRENT_REQUESTS)
        # One lock per span: a repeated span waits for the first records
        # instead of cutting and requesting into the same directory.
        self._span_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def describe(self) -> str:
        """Tool description with this session's span limit."""
        return (
            "Hear and watch the show between `start` and `end` (seconds) "
            "independently of ja.srt, in Japanese: 発話 (a speech recognizer's "
            "verbatim phrases, `[t] 話者N: text`, times in video seconds; "
            "話者 numbers only mark a change of speaker within this span), "
            "テロップ (every on-screen caption, name card and label, verbatim, "
            "read by a video model, with its time) and 場面 (a short scene "
            "description). It takes tens of seconds. The span must lie inside "
            f"the video and last at most {self._config.max_span_s:g}s."
        )

    async def call(self, start: float, end: float) -> str:
        return await anyio.to_thread.run_sync(self.watch, start, end)

    def watch(self, start: float, end: float) -> str:
        span = self._validate(start, end)
        name = span_name(span)
        with self._locks_guard:
            lock = self._span_locks.setdefault(name, threading.Lock())
        with lock:
            clip_dir = self._config.clips_dir / name
            try:
                speech = render_speech(self._speech_record(span, clip_dir))
            except (MediaError, mai.MaiError) as error:
                speech = f"## 発話\n\n（取得できず: hearing the clip failed: {error}）"
            try:
                picture = self._watch_record(span, clip_dir).text
            except (MediaError, MetaError) as error:
                picture = (
                    f"## テロップ\n\n（取得できず: watching the clip failed: {error}）"
                )
        return (
            f"Clip {span.start:.3f}s-{span.end:.3f}s (times are video seconds; "
            f"テロップ within about 2 s):\n{speech}\n\n{picture}"
        )

    def _speech_record(self, span: TimeRange, clip_dir: Path) -> SpeechRecord:
        """The kept record, else MAI's transcript; raises `MediaError` or
        `MaiError`."""
        path = clip_dir / SPEECH_FILE
        if path.is_file():
            return read_model(path, SpeechRecord)
        audio = clip_dir / WATCH_AUDIO_FILE
        slice_audio(self._runner, self._config.video, audio, span)
        transcript = self._speech.transcribe(audio, self._config.speech_phrases)
        record = SpeechRecord(
            model=mai.MODEL,
            audio_s=transcript.audio_s,
            cost_usd=transcript.cost_usd,
            phrases=[
                phrase.model_copy(
                    update={"start_s": round(span.start + phrase.start_s, 3)}
                )
                for phrase in transcript.phrases
            ],
        )
        write_model(path, record)
        return record

    def _watch_record(self, span: TimeRange, clip_dir: Path) -> WatchRecord:
        """The kept record, else Muse's answer; raises `MediaError` or
        `MetaError` (a billed one leaves a `FailedWatch`)."""
        path = clip_dir / RECORD_FILE
        if path.is_file():
            return read_model(path, WatchRecord)
        clip = clip_dir / WATCH_CLIP_FILE
        slice_preview(self._runner, self._config.video, clip, span)
        try:
            with self._requests:
                answer = self._model.ask(
                    load_prompt(__package__, "watch_clip.md"), clip
                )
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
            raise
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
