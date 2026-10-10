from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from grillmaster.agents.prompt import tools_section
from grillmaster.core.tool_session import FramesTool, ToolSession

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("window", "expected"),
    [
        pytest.param(
            (12.0, 95.5), "（時間需介於 12.000 秒與95.500 秒之間）。", id="bounded"
        ),
        pytest.param(
            (0.1234, None), "（時間需介於 0.123 秒與影片結尾之間）。", id="open"
        ),
    ],
)
def test_frames_window_uses_the_tool_description_precision(
    tmp_path: Path, window: tuple[float, float | None], expected: str
):
    # Same three decimals as `get_frames`' own description and its validator.
    frames = FramesTool(
        video=tmp_path / "video.mp4",
        frames_dir=tmp_path / "frames",
        window=window,
        max_side=640,
    )
    session = ToolSession(project_root=tmp_path, frames=frames, check_srt=None)
    assert tools_section(session).endswith(expected)
