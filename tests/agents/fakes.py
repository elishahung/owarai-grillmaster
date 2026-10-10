"""Fakes and fixture loaders shared by the agents tests."""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import claude_agent_sdk.types as sdk_types

from grillmaster.agents.adapters.base import (
    Capability,
    FinalOutput,
    MediaDelivery,
    TurnDefect,
    TurnRequest,
)
from grillmaster.core.model_spec import Backend, Effort, ModelSpec

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator, Sequence

    from grillmaster.agents.events import AgentEvent
    from grillmaster.agents.process import ProcessSpec

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "agents"
# The cheapest model of each backend: the live tests run on these, and the
# replayed recordings were made with them.
SPECS = {
    Backend.AGY: ModelSpec(Backend.AGY, "gemini-3.8-flash", Effort.LOW),
    Backend.CODEX: ModelSpec(Backend.CODEX, "gpt-6-astra", Effort.LOW),
    Backend.CLAUDE: ModelSpec(Backend.CLAUDE, "haiku", Effort.LOW),
}


def fixture_lines(backend: str, name: str) -> list[str]:
    path = FIXTURES / backend / f"{name}.jsonl"
    return path.read_text(encoding="utf-8").splitlines()


# --- process fakes (agy, codex) ------------------------------------------------


class FakeProcess:
    """Replays recorded stdout lines and records what the adapter did."""

    def __init__(
        self,
        lines: Sequence[str],
        *,
        returncode: int = 0,
        stderr: str = "",
        timed_out: bool = False,
    ) -> None:
        self._lines = list(lines)
        self._returncode = returncode
        self._stderr = stderr
        self._timed_out = timed_out
        self.lines_read = 0
        # How many lines had been read when stdin closed; None = never.
        self.stdin_closed_after: int | None = None

    def lines(self) -> Iterator[str]:
        for line in self._lines:
            self.lines_read += 1
            yield line

    def close_stdin(self) -> None:
        if self.stdin_closed_after is None:
            self.stdin_closed_after = self.lines_read

    def wait(self) -> int:
        return self._returncode

    @property
    def timed_out(self) -> bool:
        return self._timed_out

    @property
    def stderr_tail(self) -> str:
        return self._stderr


class FakeSpawn:
    """A `Spawn` handing out queued `FakeProcess`es and recording each spec."""

    def __init__(self, *processes: FakeProcess) -> None:
        self._queue = list(processes)
        self.specs: list[ProcessSpec] = []
        self.processes: list[FakeProcess] = []

    def __call__(self, spec: ProcessSpec) -> FakeProcess:
        self.specs.append(spec)
        process = self._queue.pop(0)
        self.processes.append(process)
        return process


# --- claude fakes -----------------------------------------------------------


def decode_message(data: Any) -> Any:
    """Inverse of `claude.encode_message`: rebuild the SDK dataclasses."""
    if isinstance(data, dict):
        decoded = {key: decode_message(value) for key, value in data.items()}
        type_name = decoded.pop("_type", None)
        if type_name is None:
            return decoded
        return getattr(sdk_types, type_name)(**decoded)
    if isinstance(data, list):
        return [decode_message(item) for item in data]
    return data


@dataclass
class FakeQuery:
    """A `query_fn` replaying decoded messages, then optionally failing or
    hanging (until the turn's timeout cancels it)."""

    messages: list[Any]
    failure: BaseException | None = None
    hang: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, *, prompt: Any, options: Any) -> AsyncIterator[Any]:
        self.calls.append({"prompt": prompt, "options": options})
        return self._stream()

    async def _stream(self) -> AsyncIterator[Any]:
        for message in self.messages:
            yield message
        if self.hang:
            await asyncio.sleep(60)
        if self.failure is not None:
            raise self.failure


def claude_messages(name: str) -> list[Any]:
    return [decode_message(json.loads(line)) for line in fixture_lines("claude", name)]


# --- runner fakes ---------------------------------------------------------------


@dataclass
class Turn:
    """One scripted turn: events streamed, then the final output or an error."""

    events: list[AgentEvent] = field(default_factory=list)
    final: FinalOutput | None = None
    error: BaseException | None = None
    raw: list[str] = field(default_factory=list)
    # Called when the turn starts (e.g. to write files or block).
    on_start: Callable[[TurnRequest], None] | None = None


def final(
    text: str = "done",
    *,
    structured: object | None = None,
    session_id: str = "sess-1",
    usage: dict[str, int] | None = None,
    defects: tuple[TurnDefect, ...] = (),
) -> FinalOutput:
    return FinalOutput(
        session_id=session_id,
        text=text,
        structured=structured,
        usage=usage or {},
        defects=defects,
    )


@dataclass
class Call:
    kind: str  # "start" | "resume"
    request: TurnRequest
    session_id: str | None = None


class FakeHandle:
    def __init__(self, turn: Turn, request: TurnRequest) -> None:
        self._turn = turn
        self._request = request

    def events(self) -> Iterator[AgentEvent]:
        for line in self._turn.raw:
            self._request.raw(line)
        yield from self._turn.events

    def result(self) -> FinalOutput:
        if self._turn.error is not None:
            raise self._turn.error
        assert self._turn.final is not None
        return self._turn.final


class FakeAdapter:
    """Scripted turns per task name (taken from the request's message)."""

    def __init__(
        self,
        turns: Sequence[Turn] | None = None,
        *,
        backend: Backend = Backend.CODEX,
        capabilities: frozenset[Capability] | None = None,
        media_delivery: MediaDelivery = MediaDelivery.ATTACHED,
        script: Callable[[Call], Turn] | None = None,
        preflight_error: Exception | None = None,
    ) -> None:
        self.backend = backend
        self.capabilities = (
            capabilities if capabilities is not None else frozenset(Capability)
        )
        self.media_delivery = media_delivery
        self._turns = list(turns or [])
        self._script = script
        self._preflight_error = preflight_error
        self._lock = threading.Lock()
        self.calls: list[Call] = []
        self.preflights: list[tuple[ModelSpec, tuple[Path, ...], tuple[Path, ...]]] = []

    def preflight(
        self, spec: ModelSpec, images: Sequence[Path], audio: Sequence[Path]
    ) -> None:
        self.preflights.append((spec, tuple(images), tuple(audio)))
        if self._preflight_error is not None:
            raise self._preflight_error

    def start(self, request: TurnRequest) -> FakeHandle:
        return self._handle(Call("start", request))

    def resume(self, session_id: str, request: TurnRequest) -> FakeHandle:
        return self._handle(Call("resume", request, session_id))

    def _handle(self, call: Call) -> FakeHandle:
        with self._lock:
            self.calls.append(call)
            turn = self._script(call) if self._script else self._turns.pop(0)
        if turn.on_start is not None:
            turn.on_start(call.request)
        return FakeHandle(turn, call.request)
