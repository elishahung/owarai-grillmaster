"""What a stage asks of an agent (`AgentTask`) and what it gets back (`AgentResult`).

The output spec decides how a turn's `FinalOutput` becomes the typed value:
the final text, a pydantic model from the native schema channel, or files
the agent wrote. A failed parse raises `ValidationFailure`, which the runner
answers with a repair round in the same session, exactly like a failed
`validate`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, override

from pydantic import BaseModel, ValidationError

from grillmaster.agents.errors import ValidationFailure

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from grillmaster.agents.adapters.base import Capability, FinalOutput
    from grillmaster.core.model_spec import ModelSpec, Role
    from grillmaster.core.tool_session import ToolSession


class OutputSpec[T](Protocol):
    def parse(self, final: FinalOutput, workdir: Path) -> T:
        """The typed output; raises `ValidationFailure` when it is unusable."""
        ...


@dataclass(frozen=True, slots=True)
class TextOutput(OutputSpec[str]):
    """The final assistant message."""

    @override
    def parse(self, final: FinalOutput, workdir: Path) -> str:
        if not final.text.strip():
            raise ValidationFailure("最終回覆是空的：請完整輸出結果。")
        return final.text


@dataclass(frozen=True, slots=True)
class SchemaOutput[M: BaseModel](OutputSpec[M]):
    """A `model` instance from the backend's native structured output."""

    model: type[M]

    @override
    def parse(self, final: FinalOutput, workdir: Path) -> M:
        if final.structured is None:
            raise ValidationFailure(
                "沒有收到結構化輸出：請依要求的 JSON schema，"
                "透過結構化輸出回傳完整的最終結果。"
            )
        try:
            return self.model.model_validate(final.structured)
        except ValidationError as error:
            raise ValidationFailure(str(error)) from error


@dataclass(frozen=True, slots=True)
class FilesOutput(OutputSpec[tuple[Path, ...]]):
    """Files the agent must write; relative paths resolve against the workdir.

    The runner deletes them before every fresh attempt, so an accepted file
    always comes from the session that was judged.
    """

    paths: tuple[Path, ...]

    def resolve(self, workdir: Path) -> tuple[Path, ...]:
        return tuple(workdir / path for path in self.paths)

    @override
    def parse(self, final: FinalOutput, workdir: Path) -> tuple[Path, ...]:
        resolved = self.resolve(workdir)
        missing = [str(path) for path in resolved if not path.is_file()]
        if missing:
            raise ValidationFailure(f"缺少應寫出的檔案：{'、'.join(missing)}")
        return resolved


@dataclass(frozen=True, slots=True)
class AgentTask[T]:
    # Names the task in events and logs, e.g. "chunks/0001-0119", "refine".
    name: str
    role: Role
    # Stage prompt + program rules; sent together with `prompt` as one
    # user message on every backend.
    instructions: str
    prompt: str
    # Where `prompt.md`, `tools.json`, `raw.jsonl`, `result.json` go; a
    # second attempt uses the sibling `<name>.2`.
    session_dir: Path
    # The agent's cwd and writable root; `None` is a throwaway temp dir.
    # Concurrent tasks may not share one (the runner refuses).
    workdir: Path | None
    output: OutputSpec[T]
    images: tuple[Path, ...] = ()
    audio: tuple[Path, ...] = ()
    # The grill MCP tools this task may call; written as `tools.json`.
    tools: ToolSession | None = None
    # Extra readable roots (e.g. the project root for refine).
    add_dirs: tuple[Path, ...] = ()
    validate: Callable[[T], None] | None = None
    # Capabilities the inputs cannot imply (`IMAGE_GENERATION`,
    # `WEB_SEARCH`); the runner adds those of images, audio, tools, schema.
    requires: frozenset[Capability] = frozenset()
    # Repair rounds that resume the same session with the failure message.
    max_repairs: int = 3
    # Fresh sessions tried for transient errors.
    attempts: int = 1

    def __post_init__(self) -> None:
        if self.max_repairs < 0:
            raise ValueError(f"max_repairs must be >= 0: {self.max_repairs}")
        if self.attempts < 1:
            raise ValueError(f"attempts must be >= 1: {self.attempts}")


@dataclass(frozen=True, slots=True)
class AgentResult[T]:
    output: T
    session_id: str
    spec: ModelSpec
    repairs: int
    # 1-based number of the attempt that succeeded.
    attempt: int
    elapsed_s: float
    session_dir: Path
    usage: Mapping[str, int] = field(default_factory=dict)
