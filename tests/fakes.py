"""Fakes and builders shared across test packages."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast, override

from pydantic import BaseModel, ConfigDict

from grillmaster.agents.adapters import load_adapter
from grillmaster.agents.adapters.base import Capability
from grillmaster.agents.errors import AgentOutputError, ValidationFailure
from grillmaster.agents.runner import AgentRunner
from grillmaster.agents.task import AgentResult, FilesOutput
from grillmaster.core.briefing import Briefing, SegmentSummary, TermMapping
from grillmaster.core.model_spec import Backend, Effort, ModelSpec
from grillmaster.core.srt import SrtBlock
from grillmaster.core.timecode import format_timecode_line
from grillmaster.core.tool_session import FramesTool
from grillmaster.extras.cover import COVER_NAME
from grillmaster.sources.live_chat import LIVE_CHAT_TRACK

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from typing import BinaryIO

    from grillmaster.agents.adapters.base import AgentAdapter
    from grillmaster.agents.task import AgentTask
    from grillmaster.core.model_spec import Role
    from grillmaster.events.bus import EventSink
    from grillmaster.events.types import Event
    from grillmaster.media.ffmpeg import ProgressCallback

# What `FakeFfmpeg` writes as each ffmpeg output: a JPEG SOI marker and filler.
FAKE_JPEG = b"\xff\xd8jpeg"


class FakeFfmpeg:
    """An `FfmpegRunner` that records each argv; thread-safe.

    `fail_with` is raised by every run. Otherwise ffprobe returns
    `duration` (when set) or `stdout`, and ffmpeg writes `FAKE_JPEG` at its
    last argument (unless `write_output` is off) and returns `stdout`.
    """

    def __init__(
        self,
        stdout: str = "",
        *,
        duration: float | None = None,
        write_output: bool = True,
    ) -> None:
        self.stdout = stdout
        self.duration = duration
        self.write_output = write_output
        self.fail_with: Exception | None = None
        self._lock = threading.Lock()
        self._calls: list[list[str]] = []

    @property
    def calls(self) -> list[list[str]]:
        with self._lock:
            return list(self._calls)

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        cwd: Path | None = None,
        on_progress: ProgressCallback | None = None,
        abort: threading.Event | None = None,
    ) -> str:
        with self._lock:
            self._calls.append(list(argv))
        if self.fail_with is not None:
            raise self.fail_with
        if argv[0] == "ffprobe":
            return self.stdout if self.duration is None else f"{self.duration}\n"
        if self.write_output:
            Path(argv[-1]).write_bytes(FAKE_JPEG)
        return self.stdout


def make_blocks(count: int) -> list[SrtBlock]:
    """`count` blocks numbered from 1: 1.5s cues every 2s, text `line <i>`."""
    return [
        SrtBlock(i, format_timecode_line(i * 2.0, i * 2.0 + 1.5), f"line {i}")
        for i in range(1, count + 1)
    ]


class Recorder[T]:
    """Items in arrival order; thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: list[T] = []

    def add(self, item: T) -> None:
        with self._lock:
            self._items.append(item)

    @property
    def items(self) -> list[T]:
        with self._lock:
            return list(self._items)


class RecordingSink(Recorder["Event"]):
    """An `EventSink` collecting every emitted event, in order; thread-safe."""

    def emit(self, event: Event) -> None:
        self.add(event)

    @property
    def events(self) -> list[Event]:
        return self.items


class SttResponse(BaseModel):
    """Stands in for the SDK's pydantic response model: any fields, dumped
    back as given (`None` fields dropped, like the SDK's)."""

    model_config = ConfigDict(extra="allow")


class FakeSpeechToText:
    """An ElevenLabs `SpeechToText` returning `response`; also its factory.

    Pass `fake.connect` as the `SpeechToTextFactory`: it records each API key
    it is handed. Each `convert` records its keyword arguments, with the
    uploaded file's bytes under `file`. A mapping response is wrapped in an
    `SttResponse`.
    """

    def __init__(self, response: BaseModel | Mapping[str, object]) -> None:
        self.response = (
            response
            if isinstance(response, BaseModel)
            else SttResponse.model_validate(response)
        )
        self.api_keys: list[str] = []
        self.calls: list[dict[str, object]] = []

    def connect(self, api_key: str) -> FakeSpeechToText:
        self.api_keys.append(api_key)
        return self

    def convert(
        self,
        *,
        model_id: str,
        file: BinaryIO,
        language_code: str,
        timestamps_granularity: str,
        diarize: bool,
    ) -> BaseModel:
        self.calls.append(
            {
                "model_id": model_id,
                "file": file.read(),
                "language_code": language_code,
                "timestamps_granularity": timestamps_granularity,
                "diarize": diarize,
            }
        )
        return self.response


# The spec `FakeAgentRunner` reports for a role it was given no spec for.
FAKE_SPEC = ModelSpec(Backend.AGY, "fake-model", Effort.HIGH)


@dataclass(frozen=True, slots=True)
class Rounds:
    """Scripted outputs for successive rounds of one session: each one the
    task's validator rejects costs a repair round."""

    outputs: tuple[object, ...]

    def __init__(self, *outputs: object) -> None:
        object.__setattr__(self, "outputs", outputs)


def _no_adapter(backend: Backend) -> AgentAdapter:
    raise AssertionError(f"FakeAgentRunner starts no real agent ({backend})")


class FakeAgentRunner(AgentRunner):
    """An `AgentRunner` scripted by task name; records every task, in order.

    `script[name]` is the task's output, an exception to raise, a callable
    taking the task and returning either, or `Rounds(...)`. Every output goes
    through the task's validator like a real session: a rejected output is a
    repair round (the next of `Rounds`), and running out of rounds or
    `max_repairs` raises `AgentOutputError`. Declared `FilesOutput` files
    are deleted first, like a fresh attempt. An unscripted name fails the
    test. The inherited `run_jobs` (one worker) runs the jobs one by one, in
    order.
    """

    def __init__(
        self,
        script: Mapping[str, object] | None = None,
        *,
        roles: Mapping[Role, ModelSpec] | None = None,
        events: EventSink | None = None,
    ) -> None:
        super().__init__(
            roles or {},
            _no_adapter,
            max_concurrent=1,
            timeout_s=1.0,
            events=events or RecordingSink(),
        )
        self.script: dict[str, object] = dict(script or {})
        self._spec_by_role = dict(roles or {})
        self._lock = threading.Lock()
        self._tasks: list[AgentTask[Any]] = []

    @property
    def tasks(self) -> list[AgentTask[Any]]:
        with self._lock:
            return list(self._tasks)

    @override
    def capabilities(self, role: Role) -> frozenset[Capability]:
        """The real backend's capabilities for a configured role; every
        capability for a role without a spec."""
        spec = self._spec_by_role.get(role)
        if spec is None:
            return frozenset(Capability)
        return load_adapter(spec.backend).capabilities

    def task(self, name: str) -> AgentTask[Any]:
        """The recorded task called `name`."""
        matches = [task for task in self.tasks if task.name == name]
        assert matches, f"no task named {name!r}; got {[t.name for t in self.tasks]}"
        return matches[-1]

    @override
    def run[T](self, task: AgentTask[T]) -> AgentResult[T]:
        with self._lock:
            self._tasks.append(task)
        assert task.name in self.script, f"unscripted agent task {task.name!r}"
        if isinstance(task.output, FilesOutput) and task.workdir is not None:
            for path in task.output.declared(task.workdir):
                path.unlink(missing_ok=True)
        entry = self.script[task.name]
        if callable(entry):
            entry = cast("Callable[[AgentTask[Any]], object]", entry)(task)
        if isinstance(entry, BaseException):
            raise entry
        rounds = entry.outputs if isinstance(entry, Rounds) else (entry,)
        for repairs, output in enumerate(rounds):
            if repairs > task.max_repairs:
                break
            if task.validate is not None:
                try:
                    task.validate(cast("T", output))
                except ValidationFailure:
                    continue
            return AgentResult(
                output=cast("T", output),
                session_id=f"fake-{task.name}",
                spec=self._spec_by_role.get(task.role, FAKE_SPEC),
                repairs=repairs,
                attempt=1,
                elapsed_s=0.0,
                session_dir=task.session_dir,
            )
        raise AgentOutputError(f"{task.name}: scripted output never passed validation")


# --- shared builders ----------------------------------------------------------


def make_briefing(
    *ranges: tuple[int, int],
    summary: str = "summary",
    names: Sequence[str] = (),
) -> Briefing:
    """A minimal briefing: one segment summary `seg a-b` per range, and a
    proper noun `jp<i>` -> each of `names`."""
    return Briefing(
        summary=summary,
        characters=[],
        proper_nouns=[
            TermMapping(source=f"jp{i}", target=name) for i, name in enumerate(names)
        ],
        glossary=[],
        catchphrases=[],
        tone_notes="tone",
        segment_summaries=[
            SegmentSummary(from_index=start, to_index=end, summary=f"seg {start}-{end}")
            for start, end in ranges
        ],
    )


def frames_tool(
    root: Path,
    frames_dir: Path | None = None,
    window: tuple[float, float | None] = (0.0, None),
) -> FramesTool:
    """`get_frames` over `root/video.mp4` (frames in `root/frames` by default)."""
    return FramesTool(
        video=root / "video.mp4",
        frames_dir=frames_dir or root / "frames",
        window=window,
        max_side=768,
    )


def text_item(author: str, *runs: dict[str, Any]) -> dict[str, Any]:
    """A live-chat text message renderer with the given message runs."""
    return {
        "liveChatTextMessageRenderer": {
            "authorName": {"simpleText": author},
            "message": {"runs": list(runs)},
        }
    }


def replay_line(offset_ms: int, item: dict[str, Any]) -> str:
    """One yt-dlp replay JSON line carrying `item` at `offset_ms`."""
    return json.dumps(
        {
            "replayChatItemAction": {
                "videoOffsetTimeMsec": str(offset_ms),
                "actions": [{"addChatItemAction": {"item": item}}],
            }
        },
        ensure_ascii=False,
    )


def write_replay(options: Mapping[str, Any], replay: str) -> None:
    """Play yt-dlp's live-chat download: `replay` at the track's file name."""
    stem = Path(options["outtmpl"]["default"])
    stem.with_name(f"{stem.name}.{LIVE_CHAT_TRACK}.json").write_text(
        replay, encoding="utf-8"
    )


def draws(task: AgentTask[Any]) -> tuple[Path, ...]:
    """A cover agent that writes `cover.png` into its workdir."""
    assert task.workdir is not None
    drawn = task.workdir / COVER_NAME
    drawn.write_bytes(b"png")
    return (drawn,)
