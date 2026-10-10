from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.fakes import FAKE_JPEG

from grillmaster.core.srt import SrtBlock, read_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.pipeline.reset import reset, stages_from
from grillmaster.project.state import ProjectState, Section
from grillmaster.project.store import load_state
from grillmaster.sources.errors import SourceError
from grillmaster.stages import combine
from grillmaster.stages.base import MissingArtifactError, RunOptions

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fakes import FakeFfmpeg
    from tests.stages.conftest import MakeContext

    from grillmaster.project.layout import ProjectLayout

RAW_CAPTIONS = (
    "1\n00:00:01,000 --> 00:00:03,000\nこんばんは\n\n"
    "2\n00:01:00,000 --> 00:01:02,500\nよろしく\n"
)
SECTION = Section(start=60.0, end=120.0)


@pytest.fixture
def options(request: pytest.FixtureRequest, state: ProjectState) -> RunOptions:
    """No section, unless parametrized indirectly with one."""
    section: Section = getattr(request, "param", Section())
    return RunOptions(source=state.source_id, section=section)


@pytest.fixture
def full(layout: ProjectLayout) -> Path:
    """The downloaded, joined video and the raw captions beside the parts."""
    layout.download_parts_dir.mkdir(parents=True)
    layout.full_video.write_bytes(b"full")
    (layout.download_parts_dir / "0.ja.srt").write_text(RAW_CAPTIONS, encoding="utf-8")
    return layout.full_video


def test_without_a_section_the_full_video_becomes_the_video(
    make_context: MakeContext,
    layout: ProjectLayout,
    full: Path,
    fake_ffmpeg: FakeFfmpeg,
):
    combine.STAGE.run(make_context(StageKey.COMBINE))

    assert fake_ffmpeg.calls == []
    assert layout.video.read_bytes() == b"full"
    assert not full.exists()
    assert [block.text for block in read_srt_file(layout.ja_official_srt)] == [
        "こんばんは",
        "よろしく",
    ]
    assert load_state(layout).section == Section()


@pytest.mark.parametrize("options", [SECTION], indirect=True)
def test_a_section_cuts_the_video_and_keeps_the_full_one(
    make_context: MakeContext,
    layout: ProjectLayout,
    full: Path,
    fake_ffmpeg: FakeFfmpeg,
):
    combine.STAGE.run(make_context(StageKey.COMBINE))

    (cut,) = fake_ffmpeg.calls
    assert cut[cut.index("-i") + 1] == str(full)
    assert cut[cut.index("-ss") + 1] == "60.0"
    assert cut[cut.index("-t") + 1] == "60.0"
    assert layout.video.read_bytes() == FAKE_JPEG
    assert full.read_bytes() == b"full"
    assert load_state(layout).section == SECTION
    assert read_srt_file(layout.ja_official_srt) == [
        SrtBlock(1, "00:00:00,000 --> 00:00:02,500", "よろしく")
    ]


@pytest.mark.usefixtures("full")
@pytest.mark.parametrize("options", [SECTION], indirect=True)
def test_reset_and_recut_keeps_the_download(
    make_context: MakeContext,
    layout: ProjectLayout,
    state: ProjectState,
    fake_ffmpeg: FakeFfmpeg,
):
    ctx = make_context(StageKey.COMBINE)
    combine.STAGE.run(ctx)
    state.mark_done(StageKey.COMBINE, elapsed_s=1.0)

    reset(layout, state, stages_from(StageKey.COMBINE))

    assert layout.full_video.exists()
    assert not layout.video.exists()
    assert state.section == Section()


@pytest.mark.usefixtures("full")
def test_reset_without_a_section_moves_the_video_back(
    make_context: MakeContext,
    layout: ProjectLayout,
    state: ProjectState,
    fake_ffmpeg: FakeFfmpeg,
):
    # Uncut, `video.mp4` is the moved download, its only copy.
    combine.STAGE.run(make_context(StageKey.COMBINE))
    state.mark_done(StageKey.COMBINE, elapsed_s=1.0)

    reset(layout, state, stages_from(StageKey.COMBINE))

    assert layout.full_video.read_bytes() == b"full"
    assert not layout.video.exists()
    combine.STAGE.run(make_context(StageKey.COMBINE))
    assert layout.video.read_bytes() == b"full"
    assert fake_ffmpeg.calls == []


@pytest.mark.usefixtures("full")
def test_reset_from_download_still_deletes_the_moved_video(
    make_context: MakeContext, layout: ProjectLayout, state: ProjectState
):
    combine.STAGE.run(make_context(StageKey.COMBINE))
    state.mark_done(StageKey.DOWNLOAD, elapsed_s=1.0)
    state.mark_done(StageKey.COMBINE, elapsed_s=1.0)

    reset(layout, state, stages_from(StageKey.DOWNLOAD))

    assert not layout.full_video.exists()
    assert not layout.video.exists()


def test_a_missing_full_video_names_the_reset(
    make_context: MakeContext, fake_ffmpeg: FakeFfmpeg
):
    with pytest.raises(MissingArtifactError, match="--from download"):
        combine.STAGE.run(make_context(StageKey.COMBINE))


def test_a_rerun_after_the_move_reuses_the_video(
    make_context: MakeContext, layout: ProjectLayout, fake_ffmpeg: FakeFfmpeg
):
    layout.video.write_bytes(b"moved")

    combine.STAGE.run(make_context(StageKey.COMBINE))

    assert layout.video.read_bytes() == b"moved"


@pytest.mark.usefixtures("full")
@pytest.mark.parametrize(
    "config_data", [{"features": {"official_subtitles": False}}], indirect=True
)
def test_official_subtitles_off_skips_the_captions(
    make_context: MakeContext, layout: ProjectLayout, fake_ffmpeg: FakeFfmpeg
):
    combine.STAGE.run(make_context(StageKey.COMBINE))

    assert not layout.ja_official_srt.exists()


def test_broken_captions_fail_before_the_video_moves(
    make_context: MakeContext,
    layout: ProjectLayout,
    full: Path,
    fake_ffmpeg: FakeFfmpeg,
):
    (layout.download_parts_dir / "0.ja.srt").write_text("garbage\n", encoding="utf-8")

    with pytest.raises(SourceError):
        combine.STAGE.run(make_context(StageKey.COMBINE))

    assert full.exists()
    assert not layout.video.exists()


@pytest.mark.parametrize("options", [SECTION], indirect=True)
def test_a_resume_with_a_section_warns_it_is_ignored(
    make_context: MakeContext, warnings: list[str]
):
    on_skip = combine.STAGE.on_skip
    assert on_skip is not None

    on_skip(make_context(StageKey.COMBINE))

    assert warnings == ["Video already combined; --start/--to are ignored on resume"]


def test_a_resume_without_a_section_is_silent(
    make_context: MakeContext, warnings: list[str]
):
    on_skip = combine.STAGE.on_skip
    assert on_skip is not None

    on_skip(make_context(StageKey.COMBINE))

    assert warnings == []
