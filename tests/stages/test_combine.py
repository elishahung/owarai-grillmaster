from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from loguru import logger
from tests.fakes import FAKE_JPEG
from tests.stages.conftest import ROLES

from grillmaster.config.load import LoadedConfig
from grillmaster.config.model import validate_config
from grillmaster.core.srt import SrtBlock, read_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.pipeline.reset import reset, stages_from
from grillmaster.pipeline.stage import MissingArtifactError, RunOptions
from grillmaster.project.state import ProjectState, Section
from grillmaster.project.store import load_state
from grillmaster.sources.errors import SourceError
from grillmaster.stages import combine

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from tests.fakes import FakeFfmpeg
    from tests.stages.conftest import MakeContext

    from grillmaster.config.secrets import Secrets
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
def loaded(
    request: pytest.FixtureRequest, tmp_path: Path, secrets: Secrets
) -> LoadedConfig:
    """Official subtitles on, unless parametrized indirectly with `False`."""
    official: bool = getattr(request, "param", True)
    config = validate_config(
        {"agents": {"roles": ROLES}, "features": {"official_subtitles": official}},
        root=tmp_path,
    )
    return LoadedConfig(root=tmp_path, config=config, secrets=secrets)


@pytest.fixture
def full(layout: ProjectLayout) -> Path:
    """The downloaded, joined video and the raw captions beside the parts."""
    layout.download_parts_dir.mkdir(parents=True)
    layout.full_video.write_bytes(b"full")
    (layout.download_parts_dir / "0.ja.srt").write_text(RAW_CAPTIONS, encoding="utf-8")
    return layout.full_video


@pytest.fixture
def warnings() -> Iterator[list[str]]:
    messages: list[str] = []
    handler = logger.add(
        lambda message: messages.append(message.record["message"]), level="WARNING"
    )
    yield messages
    logger.remove(handler)


def test_without_a_section_the_full_video_becomes_the_video(
    make_context: MakeContext,
    layout: ProjectLayout,
    full: Path,
    fake_ffmpeg: FakeFfmpeg,
):
    combine.build(fake_ffmpeg).run(make_context(StageKey.COMBINE))

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
    combine.build(fake_ffmpeg).run(make_context(StageKey.COMBINE))

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
    combine.build(fake_ffmpeg).run(ctx)
    state.mark_done(StageKey.COMBINE, elapsed_s=1.0)

    reset(layout, state, stages_from(StageKey.COMBINE))

    assert layout.full_video.exists()
    assert not layout.video.exists()
    assert state.section == Section()


def test_a_missing_full_video_names_the_reset(
    make_context: MakeContext, fake_ffmpeg: FakeFfmpeg
):
    with pytest.raises(MissingArtifactError, match="--from download"):
        combine.build(fake_ffmpeg).run(make_context(StageKey.COMBINE))


def test_a_rerun_after_the_move_reuses_the_video(
    make_context: MakeContext, layout: ProjectLayout, fake_ffmpeg: FakeFfmpeg
):
    layout.video.write_bytes(b"moved")

    combine.build(fake_ffmpeg).run(make_context(StageKey.COMBINE))

    assert layout.video.read_bytes() == b"moved"


@pytest.mark.usefixtures("full")
@pytest.mark.parametrize("loaded", [False], indirect=True)
def test_official_subtitles_off_skips_the_captions(
    make_context: MakeContext, layout: ProjectLayout, fake_ffmpeg: FakeFfmpeg
):
    combine.build(fake_ffmpeg).run(make_context(StageKey.COMBINE))

    assert not layout.ja_official_srt.exists()


def test_broken_captions_fail_before_the_video_moves(
    make_context: MakeContext,
    layout: ProjectLayout,
    full: Path,
    fake_ffmpeg: FakeFfmpeg,
):
    (layout.download_parts_dir / "0.ja.srt").write_text("garbage\n", encoding="utf-8")

    with pytest.raises(SourceError):
        combine.build(fake_ffmpeg).run(make_context(StageKey.COMBINE))

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


def test_definition(layout: ProjectLayout, loaded: LoadedConfig):
    assert combine.STAGE.key is StageKey.COMBINE
    assert combine.STAGE.outputs(layout) == (layout.video, layout.ja_official_srt)
    assert combine.STAGE.params(loaded.config) == {"tool": "ffmpeg"}
