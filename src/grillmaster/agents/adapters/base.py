"""The adapter contract: what a backend can do and how one turn runs.

The runner owns everything backend-neutral (the user message, session files,
repair, retries, events); an adapter only turns a `TurnRequest` into a CLI
invocation and the CLI's stream into `AgentEvent`s plus a `FinalOutput`.
Errors leave `start`/`resume`/`result` already classified as `AgentError`;
configuration errors leave `preflight`, before the runner takes a slot.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from grillmaster.agents.errors import AgentTransientError

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Mapping, Sequence
    from pathlib import Path

    from grillmaster.agents.events import AgentEvent
    from grillmaster.agents.schema import JsonSchema
    from grillmaster.core.model_spec import Backend, ModelSpec


class Capability(StrEnum):
    AUDIO_INPUT = "audio_input"
    IMAGE_INPUT = "image_input"
    IMAGE_GENERATION = "image_generation"
    NATIVE_SCHEMA = "native_schema"
    RESUME = "resume"
    MCP = "mcp"
    # Image content returned by an MCP tool reaches the model.
    MCP_IMAGE_RESULT = "mcp_image_result"
    WEB_SEARCH = "web_search"


class MediaDelivery(StrEnum):
    """How input images and audio reach the model."""

    # The adapter attaches them itself (CLI flags, SDK content blocks).
    ATTACHED = "attached"
    # The prompt lists absolute paths and the model opens each with its
    # `view_file` tool; the runner writes that instruction.
    VIEW_FILE = "view_file"


class SchemaDelivery(StrEnum):
    """How a schema-bound answer reaches the CLI's structured channel."""

    # The CLI steers the model into its structured channel itself.
    NATIVE = "native"
    # The model must call the CLI's `finish` tool with the object as its
    # arguments; a JSON text message is not submitted. The runner writes
    # that instruction.
    FINISH_TOOL = "finish_tool"


@dataclass(frozen=True, slots=True)
class McpServer:
    """The grill tool server as an MCP stdio command line."""

    command: str
    args: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TurnRequest:
    """Everything one turn needs. A resume turn repeats the start turn's
    settings (model, workdir, tools, schema) with a new `message`."""

    message: str
    spec: ModelSpec
    workdir: Path
    session_dir: Path
    timeout_s: float
    # Receives every raw stream record verbatim, for `raw.jsonl`.
    raw: Callable[[str], None]
    schema: JsonSchema | None = None
    # `schema` as the session's `schema.json`, written once by the runner.
    schema_path: Path | None = None
    images: tuple[Path, ...] = ()
    # Audio the model must hear this turn (verified by the adapter).
    audio: tuple[Path, ...] = ()
    # Extra readable roots beyond `workdir`.
    add_dirs: tuple[Path, ...] = ()
    mcp: McpServer | None = None
    # The task requires `Capability.WEB_SEARCH`.
    web_search: bool = False


@dataclass(frozen=True, slots=True)
class TurnDefect:
    """A flaw the adapter found in a finished turn (e.g. agy never opened an
    audio file). The runner answers it with a repair round like a failed
    validation: `message` is the repair text, and the media are resent."""

    message: str
    images: tuple[Path, ...] = ()
    audio: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class FinalOutput:
    session_id: str
    # The final assistant message.
    text: str
    # The native structured-output channel's JSON value; `None` when the
    # turn produced none (no schema, or the model skipped the channel).
    structured: object | None
    # Normalized token counts (see `events.normalize_usage`).
    usage: Mapping[str, int] = field(default_factory=dict)
    defects: tuple[TurnDefect, ...] = ()


class SessionHandle(Protocol):
    def events(self) -> Generator[AgentEvent]:
        """Normalized events as they stream; blocks until the turn ends.
        Closing it early ends a JSONL CLI's process tree (agy, codex); a
        Claude SDK turn runs on in the background until it finishes."""
        ...

    def result(self) -> FinalOutput:
        """The turn's outcome; drains `events()` first if needed."""
        ...


class AgentAdapter(Protocol):
    @property
    def backend(self) -> Backend: ...

    @property
    def capabilities(self) -> frozenset[Capability]: ...

    @property
    def media_delivery(self) -> MediaDelivery: ...

    @property
    def schema_delivery(self) -> SchemaDelivery: ...

    def preflight(
        self, spec: ModelSpec, images: Sequence[Path], audio: Sequence[Path]
    ) -> None:
        """Raise `AgentConfigError` for what this backend cannot run (unknown
        model, missing CLI, unsupported media); called before any slot."""
        ...

    def start(self, request: TurnRequest) -> SessionHandle: ...

    def resume(self, session_id: str, request: TurnRequest) -> SessionHandle: ...


class Turn(ABC):
    """The shared `SessionHandle.result`: drain the events, refuse a timed-out
    turn, then let the backend judge the outcome."""

    def __init__(self, request: TurnRequest) -> None:
        self._request = request

    @abstractmethod
    def events(self) -> Generator[AgentEvent]: ...

    @property
    @abstractmethod
    def timed_out(self) -> bool: ...

    def result(self) -> FinalOutput:
        for _ in self.events():
            pass
        if self.timed_out:
            raise AgentTransientError(
                f"{self._request.spec.backend} turn timed out after "
                f"{self._request.timeout_s:g}s"
            )
        return self._finish()

    @abstractmethod
    def _finish(self) -> FinalOutput:
        """The final output, or the classified `AgentError` for this turn."""
