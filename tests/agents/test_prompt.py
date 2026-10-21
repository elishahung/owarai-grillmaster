from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from grillmaster.agents.adapters.base import Media, MediaKind, ToolImageDelivery
from grillmaster.agents.prompt import (
    handoff_section,
    tools_section,
    view_file_section,
)
from grillmaster.core.tool_session import (
    FramesTool,
    ToolSession,
    WatchClipTool,
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
        project_root=tmp_path, frames=frames, check_srt=None, watch_clip=None
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


def test_the_watch_clip_line_leaves_the_limits_to_the_tool(tmp_path: Path):
    watch_clip = WatchClipTool(
        video=tmp_path / "video.mp4",
        clips_dir=tmp_path / "watch_clip",
        model="muse-spark-1.3-contributor",
        effort="high",
        env_file=tmp_path / ".env",
        timeout_s=60.0,
        max_span_s=300.0,
    )
    session = ToolSession(
        project_root=tmp_path, frames=None, check_srt=None, watch_clip=watch_clip
    )
    assert tools_section(session).endswith("回傳日文的發話、畫面字幕與場景描述。")


def test_view_file_section_lists_the_paths_by_kind(tmp_path: Path):
    clip, audio, image = tmp_path / "c.mp4", tmp_path / "a.ogg", tmp_path / "f.jpg"
    media = (
        Media(MediaKind.VIDEO, clip),
        Media(MediaKind.AUDIO, audio),
        Media(MediaKind.IMAGE, image),
    )
    # After the heading and the instruction line.
    assert view_file_section(media).splitlines()[2:] == [
        "圖片：",
        f"- {image}",
        "音訊（直接聆聽；不要用 shell 指令、腳本、ffmpeg 或語音辨識工具轉寫或分析）：",
        f"- {audio}",
        "影片（直接觀看並聆聽；不要用 shell 指令、腳本、ffmpeg 或語音辨識工具轉寫或分析）：",
        f"- {clip}",
    ]
    assert view_file_section(()) == ""


@pytest.mark.parametrize(
    ("output", "fence"),
    [('{"a": 1}', "```"), ('note\n```json\n{"a": 1}\n```', "````")],
)
def test_handoff_section_fences_the_output_beyond_its_backticks(
    output: str, fence: str
):
    text = handoff_section(output, "problem")
    assert text.endswith(f"\n{fence}\n{output}\n{fence}")
