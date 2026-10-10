from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from grillmaster.agents.adapters.base import ToolImageDelivery
from grillmaster.agents.prompt import (
    AUDIO_UNAVAILABLE_MARKER,
    audio_unavailable,
    tools_section,
)
from grillmaster.core.tool_session import FramesTool, ToolSession

if TYPE_CHECKING:
    from pathlib import Path


def _frames_session(tmp_path: Path, window: tuple[float, float | None]) -> ToolSession:
    frames = FramesTool(
        video=tmp_path / "video.mp4",
        frames_dir=tmp_path / "frames",
        window=window,
        max_side=640,
    )
    return ToolSession(project_root=tmp_path, frames=frames, check_srt=None)


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
    assert tools_section(_frames_session(tmp_path, window)).endswith(expected)


@pytest.mark.parametrize(
    ("delivery", "expected"),
    [
        (ToolImageDelivery.INLINE, "直接以圖片回傳"),
        (ToolImageDelivery.VIEW_FILE, "view_file"),
        (ToolImageDelivery.NEXT_MESSAGE, "附在下一則訊息"),
    ],
)
def test_the_frames_line_says_how_the_images_arrive(
    tmp_path: Path, delivery: ToolImageDelivery, expected: str
):
    session = _frames_session(tmp_path, (0.0, 10.0))
    assert expected in tools_section(session, delivery)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            f"{AUDIO_UNAVAILABLE_MARKER}: only the file name", "only the file name"
        ),
        pytest.param(
            f"`{AUDIO_UNAVAILABLE_MARKER}：只有檔名`", "只有檔名", id="fullwidth"
        ),
        pytest.param(f"Sorry.\n{AUDIO_UNAVAILABLE_MARKER}", "", id="bare-line"),
        pytest.param(
            f"I will not write {AUDIO_UNAVAILABLE_MARKER} here", None, id="inline"
        ),
        pytest.param("I heard everything.", None, id="absent"),
    ],
)
def test_audio_unavailable_reads_only_a_marker_line(text: str, expected: str | None):
    assert audio_unavailable(text) == expected
