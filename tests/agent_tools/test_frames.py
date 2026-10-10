from __future__ import annotations

import base64
import math
from typing import TYPE_CHECKING

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ImageContent, TextContent
from tests.fakes import FAKE_JPEG

from grillmaster.agent_tools.frames import MAX_FRAMES_PER_CALL, FrameGrabber
from grillmaster.media.errors import MediaError

if TYPE_CHECKING:
    from tests.fakes import FakeFfmpeg

    from grillmaster.core.tool_session import FramesTool


@pytest.fixture
def grabber(frames_config: FramesTool, fake_ffmpeg: FakeFfmpeg) -> FrameGrabber:
    return FrameGrabber(frames_config, fake_ffmpeg)


def test_returns_a_listing_then_one_image_per_frame(
    grabber: FrameGrabber, frames_config: FramesTool
):
    header, *images = grabber.get_frames([42.0, 12.5, 12.5])
    assert isinstance(header, TextContent)
    first = frames_config.frames_dir / "frame_000012.500_768.jpg"
    second = frames_config.frames_dir / "frame_000042.000_768.jpg"
    assert header.text.splitlines()[1:] == [f"12.500s: {first}", f"42.000s: {second}"]
    assert len(images) == 2
    for image in images:
        assert isinstance(image, ImageContent)
        assert image.mimeType == "image/jpeg"
        assert base64.b64decode(image.data) == FAKE_JPEG
    assert first.exists()
    assert second.exists()


def test_duration_is_probed_once(grabber: FrameGrabber, fake_ffmpeg: FakeFfmpeg):
    grabber.get_frames([11.0])
    grabber.get_frames([12.0])
    assert [argv[0] for argv in fake_ffmpeg.calls] == ["ffprobe", "ffmpeg", "ffmpeg"]


def test_timestamps_dedupe_at_millisecond_precision(grabber: FrameGrabber):
    header, *images = grabber.get_frames([20.0001, 20.0002])
    assert len(images) == 1
    assert isinstance(header, TextContent)
    assert "20.000s" in header.text


def test_empty_request_is_rejected(grabber: FrameGrabber):
    with pytest.raises(ToolError, match="at least one"):
        grabber.get_frames([])


def test_too_many_timestamps_are_rejected(
    grabber: FrameGrabber, fake_ffmpeg: FakeFfmpeg
):
    times = [10.0 + i for i in range(MAX_FRAMES_PER_CALL + 1)]
    with pytest.raises(ToolError, match=f"at most {MAX_FRAMES_PER_CALL} per call"):
        grabber.get_frames(times)
    assert fake_ffmpeg.calls == []


# The video length is exclusive (a seek to the very end extracts nothing), and
# timestamps are checked at the millisecond precision they are extracted at.
@pytest.mark.parametrize("bad", [9.5, 100.0, 99.9999, 100.5, math.nan, math.inf])
def test_timestamps_outside_the_window_are_rejected(
    grabber: FrameGrabber, fake_ffmpeg: FakeFfmpeg, bad: float
):
    with pytest.raises(
        ToolError, match=r"outside the allowed window 10\.000s-100\.000s"
    ):
        grabber.get_frames([50.0, bad])
    assert [argv[0] for argv in fake_ffmpeg.calls] == ["ffprobe"]


def test_last_millisecond_before_the_end_is_allowed(grabber: FrameGrabber):
    _header, *images = grabber.get_frames([10.0, 99.999])
    assert len(images) == 2


def test_window_end_caps_the_range(frames_config: FramesTool, fake_ffmpeg: FakeFfmpeg):
    narrow = FrameGrabber(
        frames_config.model_copy(update={"window": (10.0, 30.0)}), fake_ffmpeg
    )
    assert "30.000s" in narrow.describe()
    with pytest.raises(ToolError, match=r"10\.000s-30\.000s: 31"):
        narrow.get_frames([31.0])
    _header, *images = narrow.get_frames([30.0])  # a window end is inclusive
    assert len(images) == 1


def test_media_failure_becomes_a_tool_error(
    grabber: FrameGrabber, fake_ffmpeg: FakeFfmpeg
):
    grabber.get_frames([11.0])  # probe succeeds first
    fake_ffmpeg.fail_with = MediaError("ffmpeg failed (exit 1)")
    with pytest.raises(ToolError, match="ffmpeg failed"):
        grabber.get_frames([12.0])


def test_description_states_the_window_and_limit(grabber: FrameGrabber):
    text = grabber.describe()
    assert "10.000s and the end of the video" in text
    assert f"at most {MAX_FRAMES_PER_CALL}" in text
