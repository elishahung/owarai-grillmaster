from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from tests.fakes import FAKE_JPEG

from grillmaster.core.stage_key import StageKey
from grillmaster.stages import audio
from grillmaster.stages.base import MissingArtifactError

if TYPE_CHECKING:
    from tests.fakes import FakeFfmpeg
    from tests.stages.conftest import MakeContext

    from grillmaster.project.layout import ProjectLayout


def test_extracts_the_video_audio_into_the_stage_workdir(
    make_context: MakeContext, layout: ProjectLayout, fake_ffmpeg: FakeFfmpeg
):
    layout.video.write_bytes(b"video")

    audio.STAGE.run(make_context(StageKey.AUDIO))

    (argv,) = fake_ffmpeg.calls
    assert argv[argv.index("-i") + 1] == str(layout.video)
    assert Path(argv[-1]).parent == layout.work_dir(StageKey.AUDIO)
    assert layout.audio.read_bytes() == FAKE_JPEG


def test_a_missing_video_names_the_reset(
    make_context: MakeContext, fake_ffmpeg: FakeFfmpeg
):
    with pytest.raises(MissingArtifactError, match="--from combine"):
        audio.STAGE.run(make_context(StageKey.AUDIO))
    assert fake_ffmpeg.calls == []
