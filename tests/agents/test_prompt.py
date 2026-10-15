from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from grillmaster.agents.adapters.base import ToolImageDelivery
from grillmaster.agents.prompt import (
    handoff_section,
    tools_section,
)
from grillmaster.core.model_spec import ModelSpec
from grillmaster.core.tool_session import (
    FramesTool,
    ListenTool,
    ToolSession,
)

if TYPE_CHECKING:
    from pathlib import Path


def _frames_session(tmp_path: Path, window: tuple[float, float | None]) -> ToolSession:
    frames = FramesTool(
        video=tmp_path / "video.mp4",
        frames_dir=tmp_path / "frames",
        window=window,
        max_side=640,
    )
    return ToolSession(
        project_root=tmp_path, frames=frames, check_srt=None, listen=None
    )


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


def test_the_listen_line_leaves_the_limits_to_the_tool(tmp_path: Path):
    listen = ListenTool(
        audio=tmp_path / "audio.ogg",
        clips_dir=tmp_path / "listen",
        model=ModelSpec.parse("gemini/gemini-3.8-flash/low"),
        timeout_s=60.0,
        max_span_s=300.0,
    )
    session = ToolSession(
        project_root=tmp_path, frames=None, check_srt=None, listen=listen
    )
    assert tools_section(session).endswith("以純文字回傳日文逐字稿。")


@pytest.mark.parametrize(
    ("output", "fence"),
    [('{"a": 1}', "```"), ('note\n```json\n{"a": 1}\n```', "````")],
)
def test_handoff_section_fences_the_output_beyond_its_backticks(
    output: str, fence: str
):
    text = handoff_section(output, "problem")
    assert text.endswith(f"\n{fence}\n{output}\n{fence}")
