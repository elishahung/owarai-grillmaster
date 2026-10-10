from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.fakes import make_blocks

from grillmaster.core.srt import write_srt_file
from grillmaster.core.tool_session import FramesTool, SrtCheckTool, ToolSession

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fakes import FakeFfmpeg

    from grillmaster.core.srt import SrtBlock

VIDEO_SECONDS = 100.0


@pytest.fixture
def fake_ffmpeg(fake_ffmpeg: FakeFfmpeg) -> FakeFfmpeg:
    """The shared fake, probing as a `VIDEO_SECONDS`-long video."""
    fake_ffmpeg.duration = VIDEO_SECONDS
    return fake_ffmpeg


@pytest.fixture
def reference_blocks() -> list[SrtBlock]:
    return make_blocks(3)


@pytest.fixture
def frames_config(tmp_path: Path) -> FramesTool:
    """Frames start at 10s and run to the end of the video."""
    return FramesTool(
        video=tmp_path / "video.mp4",
        frames_dir=tmp_path / "work" / "10_refine" / "frames",
        window=(10.0, None),
        max_side=768,
    )


@pytest.fixture
def srt_config(tmp_path: Path, reference_blocks: list[SrtBlock]) -> SrtCheckTool:
    """The reference has three blocks."""
    reference = tmp_path / "work" / "09_chunks" / "merged.srt"
    write_srt_file(reference, reference_blocks)
    return SrtCheckTool(reference_srt=reference)


@pytest.fixture
def session(
    tmp_path: Path, frames_config: FramesTool, srt_config: SrtCheckTool
) -> ToolSession:
    """Both tools enabled."""
    return ToolSession(
        project_root=tmp_path, frames=frames_config, check_srt=srt_config
    )
