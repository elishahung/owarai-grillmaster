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

from pydantic import model_validator

from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.core.models import FrozenModel

SESSION_ENV_VAR = "GRILL_TOOL_SESSION"


class ToolName(StrEnum):
    GET_FRAMES = "get_frames"
    CHECK_SRT = "check_srt"


class FramesTool(FrozenModel):
    """`get_frames`: stills from `video`, cached under `frames_dir`."""

    video: Path
    frames_dir: Path
    # Seconds; `None` as the end means "to the end of the video".
    window: tuple[float, float | None]
    max_side: int

    @model_validator(mode="after")
    def _check_ranges(self) -> FramesTool:
        start, end = self.window
        if start < 0 or (end is not None and end < start):
            raise ValueError(f"Invalid tool window: {self.window}")
        if self.max_side <= 0:
            raise ValueError(f"max_side must be positive: {self.max_side}")
        return self


class SrtCheckTool(FrozenModel):
    """`check_srt`: the candidate must keep the reference's block count,
    indexes and timecodes."""

    reference_srt: Path


class ToolSession(FrozenModel):
    project_root: Path
    frames: FramesTool | None
    check_srt: SrtCheckTool | None

    @property
    def allowed(self) -> frozenset[ToolName]:
        """The tools this session exposes: those with a sub-config."""
        tools = {
            ToolName.GET_FRAMES: self.frames,
            ToolName.CHECK_SRT: self.check_srt,
        }
        return frozenset(name for name, config in tools.items() if config is not None)

    def write(self, path: Path) -> None:
        write_model(path, self)

    @classmethod
    def load(cls, path: Path) -> ToolSession:
        """Read a manifest; raises on a missing or invalid file."""
        return read_model(path, cls)
