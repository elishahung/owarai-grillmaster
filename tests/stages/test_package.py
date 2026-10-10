from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest
from tests.fakes import make_briefing
from tests.package.conftest import PackageFfmpeg, arg_after, encodes

from grillmaster.core.json_artifact import write_model
from grillmaster.core.srt import SrtBlock, read_srt_file, write_srt_file
from grillmaster.core.timecode import format_timecode_line
from grillmaster.extras.titles import TitleSuggestion, TitleSuggestions
from grillmaster.live_chat.layout import ChatLayout
from grillmaster.live_chat.schema import TranslatedChatLog, TranslatedChatMessage
from grillmaster.media.errors import MediaError
from grillmaster.package.errors import PackageError, PoolError
from grillmaster.package.pools import CURSOR_FILE_NAME
from grillmaster.package.remix import select_remix_segments
from grillmaster.package.render import (
    PACKAGE_LEAD_TRIM_SECONDS,
    package_output_duration,
)
from grillmaster.pipeline.state_store import StateStore
from grillmaster.stages import package
from grillmaster.stages.base import RunOptions, StageContext

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tests.fakes import FakeAgentRunner, FakeFfmpeg

    from grillmaster.config.load import LoadedConfig
    from grillmaster.events.bus import EventBus
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState
    from grillmaster.stages.base import Externals

    type MakeContext = Callable[..., StageContext]

SOURCE_SECONDS = 1000.0
BRIEFING = make_briefing(summary="demo")
TITLES = TitleSuggestions(
    titles=[TitleSuggestion(title=f"標題{i}", reason="r") for i in range(3)]
)
DESTINATION = "epabc123_show"
# Where the deliverable is rendered before it replaces DESTINATION.
STAGING = f"{DESTINATION}.partial"


@pytest.fixture
def package_root(tmp_path: Path) -> Path:
    return tmp_path / "deliverables"


@pytest.fixture
def config_sections(package_root: Path) -> dict[str, Any]:
    return {
        "paths": {"package": str(package_root)},
        "package": {
            "remix_pool": "noise",
            "inserts": [{"pool": "judge", "output": "judge", "when": "remix"}],
        },
    }


@pytest.fixture
def state(state: ProjectState) -> ProjectState:
    state.name = "show"
    return state


@pytest.fixture
def script() -> dict[str, object]:
    return {"titles": TITLES}


@pytest.fixture
def finished(layout: ProjectLayout) -> ProjectLayout:
    """A project whose stages all ran: video, subtitles, briefing, reports."""
    layout.subs_dir.mkdir(parents=True)
    layout.video.write_bytes(b"video")
    layout.cht_ass.write_text("ass", encoding="utf-8")
    write_srt_file(
        layout.cht_srt,
        [
            SrtBlock(1, format_timecode_line(0.0, 490.0), "一"),
            SrtBlock(2, format_timecode_line(510.0, 1000.0), "二"),
        ],
    )
    layout.poster.write_bytes(b"poster")
    write_model(layout.prepass_briefing, BRIEFING)
    layout.refine_report.parent.mkdir(parents=True)
    layout.refine_report.write_text("refine report", encoding="utf-8")
    return layout


@pytest.fixture
def make(
    *,
    layout: ProjectLayout,
    state: ProjectState,
    loaded: LoadedConfig,
    agents: FakeAgentRunner,
    bus: EventBus,
    externals: Externals,
) -> MakeContext:
    """The package step's context over `ffmpeg`, with run options `options`."""

    def build(ffmpeg: FakeFfmpeg, **options: Any) -> StageContext:
        return StageContext(
            layout=layout,
            store=StateStore(layout, state),
            loaded=loaded,
            options=RunOptions(source=state.source_id, **options),
            agents=agents,
            events=bus,
            externals=replace(externals, ffmpeg=ffmpeg),
            workdir=package.STEP.workdir(layout),
        )

    return build


def burn_in_fake(layout: ProjectLayout, package_root: Path) -> PackageFfmpeg:
    output = package_root / STAGING / package.VIDEO_NAME
    expected = package_output_duration(SOURCE_SECONDS - PACKAGE_LEAD_TRIM_SECONDS)
    return PackageFfmpeg({layout.video: SOURCE_SECONDS, output: expected})


def remix_fake(
    layout: ProjectLayout, package_root: Path, *, noise_seconds: float = 60.0
) -> PackageFfmpeg:
    """Pools whose files all last `noise_seconds` (one whole file per cut)
    and remix parts of the right length."""
    segments = select_remix_segments(
        [block.time_range for block in read_srt_file(layout.cht_srt)],
        SOURCE_SECONDS,
        start_seconds=PACKAGE_LEAD_TRIM_SECONDS,
    )
    parts = {
        package_root / STAGING / f"{index}.mp4": noise_seconds
        + package_output_duration(segment.duration)
        for index, segment in enumerate(segments, start=1)
    }
    return PackageFfmpeg(
        {layout.video: SOURCE_SECONDS, **parts}, default_duration=noise_seconds
    )


def make_pool(package_root: Path, name: str, count: int) -> Path:
    directory = package_root / "pools" / name
    directory.mkdir(parents=True)
    for index in range(1, count + 1):
        (directory / f"{index:03d}.mp4").write_text(f"{name} {index}", encoding="utf-8")
    return directory


def test_burns_in_and_copies_the_artifacts_first(
    make: MakeContext, finished: ProjectLayout, package_root: Path
):
    fake = burn_in_fake(finished, package_root)

    result = package.STEP.run(make(fake))

    target = package_root / DESTINATION
    assert result == str(target)
    assert sorted(path.name for path in target.iterdir()) == [
        "cover.jpg",
        "info.json",
        "refine.md",
        "video.mp4",
    ]
    assert json.loads((target / "info.json").read_text("utf-8")) == (
        BRIEFING.prompt_dict()
    )
    # Plain burn-in: no remix, so the remix-only insert is not copied.
    assert not (package_root / "pools").exists()
    assert fake.cwds[0] == finished.root
    assert not finished.titles.exists()


def test_existing_titles_lead_the_info(
    make: MakeContext, finished: ProjectLayout, package_root: Path
):
    write_model(finished.titles, TITLES)

    package.STEP.run(make(burn_in_fake(finished, package_root)))

    info = json.loads((package_root / DESTINATION / "info.json").read_text("utf-8"))
    assert list(info)[:2] == ["titles", "summary"]


@pytest.mark.parametrize(
    "config_data",
    [
        pytest.param(
            {
                "package": {},
                "features": {"title_suggestion": True},
            },
            id="titles-on",
        )
    ],
    indirect=True,
)
def test_title_suggestion_generates_and_caches_titles(
    make: MakeContext,
    finished: ProjectLayout,
    package_root: Path,
    agents: FakeAgentRunner,
):
    package.STEP.run(make(burn_in_fake(finished, package_root)))

    task = agents.task("titles")
    assert task.session_dir == finished.package_work_dir / "session"
    assert finished.titles.exists()
    info = json.loads((package_root / DESTINATION / "info.json").read_text("utf-8"))
    assert info["titles"][0]["title"] == "標題0"


def test_remix_renders_parts_and_carries_the_insert(
    make: MakeContext, finished: ProjectLayout, package_root: Path
):
    noise = make_pool(package_root, "sleep", 2)
    judge = make_pool(package_root, "judge", 3)
    (judge / CURSOR_FILE_NAME).write_text('{"index": 1}', encoding="utf-8")
    fake = remix_fake(finished, package_root)

    package.STEP.run(make(fake, remix="sleep"))

    target = package_root / DESTINATION
    assert sorted(path.name for path in target.iterdir()) == [
        "1.mp4",
        "2.mp4",
        "cover.jpg",
        "info.json",
        "judge.mp4",
        "refine.md",
    ]
    assert (target / "judge.mp4").read_text(encoding="utf-8") == "judge 2"
    assert json.loads((noise / CURSOR_FILE_NAME).read_text("utf-8"))["index"] == 0


@pytest.mark.parametrize(
    "config_data",
    [
        pytest.param(
            {
                "package": {"remix_pool": "noise"},
                "programs": {"series": {"ドキュメンタル": {"remix": True}}},
            },
            id="remix-series",
        )
    ],
    indirect=True,
)
def test_a_remix_program_forces_a_remix_with_the_default_pool(
    make: MakeContext,
    finished: ProjectLayout,
    package_root: Path,
    state: ProjectState,
):
    state.source.series = "ドキュメンタル"
    make_pool(package_root, "noise", 1)

    package.STEP.run(make(remix_fake(finished, package_root)))

    assert (package_root / DESTINATION / "1.mp4").exists()
    assert not (package_root / DESTINATION / package.VIDEO_NAME).exists()


@pytest.mark.parametrize(
    "config_data",
    [
        pytest.param(
            {
                "package": {
                    "inserts": [{"pool": "ending", "output": "outro", "when": "always"}]
                },
            },
            id="always-insert",
        )
    ],
    indirect=True,
)
def test_an_always_insert_rides_along_a_plain_burn_in(
    make: MakeContext, finished: ProjectLayout, package_root: Path
):
    make_pool(package_root, "ending", 1)

    package.STEP.run(make(burn_in_fake(finished, package_root)))

    assert (package_root / DESTINATION / "outro.mp4").exists()


def test_a_missing_insert_pool_fails_before_any_draw(
    make: MakeContext, finished: ProjectLayout, package_root: Path
):
    noise = make_pool(package_root, "sleep", 1)

    with pytest.raises(PoolError, match="judge"):
        package.STEP.run(
            make(PackageFfmpeg({finished.video: SOURCE_SECONDS}), remix="sleep")
        )
    assert not (package_root / DESTINATION).exists()
    assert not (package_root / STAGING).exists()
    assert not (noise / CURSOR_FILE_NAME).exists()


def test_a_missing_noise_pool_draws_no_insert(
    make: MakeContext, finished: ProjectLayout, package_root: Path
):
    judge = make_pool(package_root, "judge", 2)

    with pytest.raises(PoolError, match="sleep"):
        package.STEP.run(
            make(PackageFfmpeg({finished.video: SOURCE_SECONDS}), remix="sleep")
        )
    assert not (judge / CURSOR_FILE_NAME).exists()
    assert not (package_root / STAGING).exists()


def test_an_unsplittable_remix_draws_nothing(
    make: MakeContext, finished: ProjectLayout, package_root: Path
):
    noise = make_pool(package_root, "sleep", 1)
    judge = make_pool(package_root, "judge", 2)
    write_srt_file(finished.cht_srt, [])

    with pytest.raises(PackageError, match="no subtitle time ranges"):
        package.STEP.run(
            make(PackageFfmpeg({finished.video: SOURCE_SECONDS}), remix="sleep")
        )
    assert not (noise / CURSOR_FILE_NAME).exists()
    assert not (judge / CURSOR_FILE_NAME).exists()
    assert not (package_root / STAGING).exists()


def test_a_failed_render_removes_the_folder(
    make: MakeContext, finished: ProjectLayout, package_root: Path
):
    fake = burn_in_fake(finished, package_root)
    fake.fail_encodes = MediaError("ffmpeg failed")

    with pytest.raises(MediaError):
        package.STEP.run(make(fake))
    assert not (package_root / DESTINATION).exists()
    assert not (package_root / STAGING).exists()


def test_a_failed_repackage_keeps_the_previous_deliverable(
    make: MakeContext, finished: ProjectLayout, package_root: Path
):
    package.STEP.run(make(burn_in_fake(finished, package_root)))
    previous = sorted(path.name for path in (package_root / DESTINATION).iterdir())
    fake = burn_in_fake(finished, package_root)
    fake.fail_encodes = MediaError("ffmpeg failed")

    with pytest.raises(MediaError):
        package.STEP.run(make(fake))

    target = package_root / DESTINATION
    assert sorted(path.name for path in target.iterdir()) == previous
    assert sorted(path.name for path in package_root.iterdir()) == [DESTINATION]


def test_a_repackage_replaces_the_previous_deliverable(
    make: MakeContext, finished: ProjectLayout, package_root: Path
):
    stale = package_root / DESTINATION / "stale.mp4"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")

    package.STEP.run(make(burn_in_fake(finished, package_root)))

    assert not stale.exists()
    assert (package_root / DESTINATION / package.VIDEO_NAME).exists()
    assert sorted(path.name for path in package_root.iterdir()) == [DESTINATION]


def test_missing_subtitles_fail_before_anything_is_created(
    make: MakeContext, finished: ProjectLayout, package_root: Path
):
    finished.cht_ass.unlink()

    with pytest.raises(PackageError, match=r"cht\.ass"):
        package.STEP.run(make(PackageFfmpeg()))
    assert not package_root.exists()


def test_chat_panel_is_rendered_into_the_package_workdir(
    make: MakeContext, finished: ProjectLayout, package_root: Path
):
    log = TranslatedChatLog(
        messages=[
            TranslatedChatMessage(
                id=0, seconds=1.0, author="@a", text="池田", translation="池田"
            )
        ]
    )
    write_model(finished.chat_cht_json, log)
    fake = burn_in_fake(finished, package_root)

    package.STEP.run(make(fake, chat_layout=ChatLayout.SIDE))

    assert finished.chat_panel_ass.exists()
    chain = arg_after(next(a for a in encodes(fake) if "-vf" in a), "-vf")
    assert (
        "pad=1920:1080:0:108:black,subtitles=work/package/chat.ass,"
        "subtitles=subs/cht.ass:force_style='MarginR=394,MarginV=24',"
    ) in chain


def test_enabled_only_with_a_package_root(loaded: LoadedConfig, state: ProjectState):
    options = RunOptions(source=state.source_id)
    assert package.STEP.enabled(options, loaded.config)
    unset = loaded.config.model_copy(
        update={"paths": loaded.config.paths.model_copy(update={"package": None})}
    )
    assert not package.STEP.enabled(options, unset)
