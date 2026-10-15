"""The MCP tool-session manifest (`tools.json`): agents write it, tools read it.

Each agent call gets one manifest in its session directory. The tool server
finds it through `--session <path>` or the `GRILL_TOOL_SESSION` environment
variable and exposes only the tools whose sub-config is present, scoped to
that config's window and reference files. A server without a readable
manifest is a hard error, so `load` raises instead of treating a missing file
as a miss.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import model_validator

from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.core.model_spec import ModelSpecField
from grillmaster.core.models import FrozenModel

if TYPE_CHECKING:
    from collections.abc import Iterable

SESSION_ENV_VAR = "GRILL_TOOL_SESSION"


class ToolName(StrEnum):
    GET_FRAMES = "get_frames"
    CHECK_SRT = "check_srt"
    LISTEN = "listen"


class FramesTool(FrozenModel):
    """`get_frames`: stills from `video`, cached under `frames_dir`.

    With `pending_frames` (set by the agent runner for a backend whose tool
    results cannot carry images) the tool returns text only and appends each
    frame's path to that file, one per line; the runner attaches them to the
    session's next message."""

    video: Path
    frames_dir: Path
    # Seconds; `None` as the end means "to the end of the video".
    window: tuple[float, float | None]
    max_side: int
    pending_frames: Path | None = None

    @model_validator(mode="after")
    def _check_ranges(self) -> FramesTool:
        start, end = self.window
        if start < 0 or (end is not None and end < start):
            raise ValueError(f"Invalid tool window: {self.window}")
        if self.max_side <= 0:
            raise ValueError(f"max_side must be positive: {self.max_side}")
        return self


def leave_pending_frames(path: Path, frames: Iterable[Path]) -> None:
    """Add `frames` to the pending list at `path` (`FramesTool.pending_frames`)."""
    with path.open("a", encoding="utf-8") as handle:
        handle.writelines(f"{frame}\n" for frame in frames)


def take_pending_frames(path: Path) -> tuple[Path, ...]:
    """The frames pending at `path`, once each; the list is removed so a
    frame is delivered once. Empty when nothing is pending."""
    if not path.is_file():
        return ()
    lines = path.read_text(encoding="utf-8").splitlines()
    path.unlink()
    return tuple(dict.fromkeys(Path(line) for line in lines if line.strip()))


class SrtCheckTool(FrozenModel):
    """`check_srt`: the candidate must keep the reference's block count,
    indexes and timecodes."""

    reference_srt: Path


class ListenTool(FrozenModel):
    """`listen`: a fresh transcription of a span of `audio` by `model`.

    The tool server runs the transcription as its own agent session (the
    `listen` role, `model` written `backend/model[/effort]`, each turn bounded
    by `timeout_s`); a span's clip, transcript and session records go to
    `<clips_dir>/<start>-<end>/`, and an existing transcript is returned
    again without a new session."""

    audio: Path
    clips_dir: Path
    model: ModelSpecField
    timeout_s: float
    # The longest span one call transcribes, anywhere in the audio.
    max_span_s: float

    @model_validator(mode="after")
    def _check_limits(self) -> ListenTool:
        if self.max_span_s <= 0 or self.timeout_s <= 0:
            raise ValueError(
                f"max_span_s and timeout_s must be positive: "
                f"{self.max_span_s}, {self.timeout_s}"
            )
        return self


class ToolSession(FrozenModel):
    project_root: Path
    frames: FramesTool | None
    check_srt: SrtCheckTool | None
    listen: ListenTool | None

    @property
    def allowed(self) -> frozenset[ToolName]:
        """The tools this session exposes: those with a sub-config."""
        tools = {
            ToolName.GET_FRAMES: self.frames,
            ToolName.CHECK_SRT: self.check_srt,
            ToolName.LISTEN: self.listen,
        }
        return frozenset(name for name, config in tools.items() if config is not None)

    def write(self, path: Path) -> None:
        write_model(path, self)

    @classmethod
    def load(cls, path: Path) -> ToolSession:
        """Read a manifest; raises on a missing or invalid file."""
        return read_model(path, cls)
