"""`SplitRun`: `grill <src> --parts N` over `run_project` and `SerialRun`."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import TYPE_CHECKING

import pytest
from tests.fakes import FakeFfmpeg
from tests.pipeline.fakes import Journal, StageFailedError, fake_stage

from grillmaster.core.source_id import Platform, SourceId, parse_source
from grillmaster.core.srt import read_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.pipeline.registry import Pipeline
from grillmaster.pipeline.reset import reset, stages_from
from grillmaster.pipeline.split import (
    SplitError,
    SplitRun,
    part_sections,
    part_sources,
)
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.state import PartOrigin, ProjectState, Section, SourceInfo
from grillmaster.project.store import create_project, load_state, save_state
from grillmaster.sources.errors import SourceError
from grillmaster.stages.base import RunOptions

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from tests.fakes import RecordingSink

    from grillmaster.config.load import LoadedConfig
    from grillmaster.stages.base import StageContext

    type RunSplit = Callable[..., ProjectLayout | None]

SOURCE = SourceId(Platform.BILIBILI, "BVlong1")
CAPTIONS = "1\n00:00:01,000 --> 00:00:02,000\nearly\n\n2\n00:40:00,000 --> 00:40:02,000\nlate\n\n"


@pytest.fixture
def journal() -> Journal:
    return Journal()


def pipeline(
    journal: Journal, on_asr: Callable[[StageContext], None] | None = None
) -> Pipeline:
    """Metadata and download as a real run leaves them; `asr` stands for every
    stage after the split, `combine` must never run on a part."""

    def metadata(ctx: StageContext) -> None:
        def record(state: ProjectState) -> None:
            state.name = "Long_Talk"
            state.broadcast_date = date(2026, 11, 6)
            state.source = SourceInfo(title="Long Talk")

        ctx.update(record)

    def download(ctx: StageContext) -> None:
        ctx.layout.full_video.parent.mkdir(parents=True, exist_ok=True)
        ctx.layout.full_video.write_bytes(b"video")
        ctx.layout.poster.write_bytes(b"poster")
        ctx.layout.download_parts_dir.mkdir(parents=True, exist_ok=True)
        (ctx.layout.download_parts_dir / "0.ja.srt").write_text(
            CAPTIONS, encoding="utf-8"
        )

    return Pipeline(
        (
            fake_stage(StageKey.METADATA, journal, action=metadata),
            fake_stage(StageKey.DOWNLOAD, journal, action=download),
            fake_stage(StageKey.COMBINE, journal),
            fake_stage(StageKey.ASR, journal, action=on_asr),
        )
    )


def split(ffmpeg: FakeFfmpeg, parts: int = 2, **options: object) -> SplitRun:
    template = RunOptions(source=SOURCE, **options)  # pyright: ignore[reportArgumentType]
    return SplitRun(template, parts, ffmpeg=ffmpeg)


@pytest.fixture
def run_split(
    loaded: LoadedConfig, journal: Journal, recording_sink: RecordingSink
) -> RunSplit:
    def run(
        ffmpeg: FakeFfmpeg | None = None,
        *,
        on_asr: Callable[[StageContext], None] | None = None,
        **split_options: object,
    ) -> ProjectLayout | None:
        return split(ffmpeg or FakeFfmpeg(duration=3600.0), **split_options).run(  # pyright: ignore[reportArgumentType]
            loaded, sinks=[recording_sink], pipeline=pipeline(journal, on_asr)
        )

    return run


def failing_on(*ids: str) -> tuple[Callable[[StageContext], None], set[str]]:
    """An `asr` action failing for `ids`, and the set to clear to let it pass."""
    failures = set(ids)

    def on_asr(ctx: StageContext) -> None:
        if ctx.state.id in failures:
            raise StageFailedError("boom")

    return on_asr, failures


def part_layouts(loaded: LoadedConfig) -> list[ProjectLayout]:
    return [
        ProjectLayout.for_id(loaded.projects_root, source.video_id)
        for source in part_sources(SOURCE, 2)
    ]


def cuts(ffmpeg: FakeFfmpeg) -> list[list[str]]:
    return [call for call in ffmpeg.calls if call[0] != "ffprobe"]


def test_part_ids_round_trip_through_their_urls():
    for source in part_sources(SOURCE, 2):
        assert parse_source(source.url) == source


def test_sections_cover_the_whole_video():
    assert part_sections(90.0, 3) == (
        Section(end=30.0),
        Section(start=30.0, end=60.0),
        Section(start=60.0),
    )


def test_download_then_a_chain_of_parts(
    loaded: LoadedConfig, journal: Journal, run_split: RunSplit
):
    ffmpeg = FakeFfmpeg(duration=3600.0)

    final = run_split(ffmpeg, hint="節目資訊")

    first, second = part_layouts(loaded)
    assert final == second
    assert not ProjectLayout.for_id(loaded.projects_root, "BVlong1").root.exists()
    assert journal.entries == [
        "run:metadata@metadata",
        "run:download@download",
        "on_skip:metadata",
        "on_skip:download",
        "on_skip:combine",
        "run:asr@asr",
        "on_skip:metadata",
        "on_skip:download",
        "on_skip:combine",
        "run:asr@asr",
    ]
    states = [load_state(first), load_state(second)]
    assert [state.id for state in states] == ["BVlong1p1", "BVlong1p2"]
    assert [state.name for state in states] == ["Long_Talk_1", "Long_Talk_2"]
    assert [state.source.title for state in states] == [
        "Long Talk Part 1",
        "Long Talk Part 2",
    ]
    assert {state.broadcast_date for state in states} == {date(2026, 11, 6)}
    assert states[0].section == Section(end=1800.0)
    assert states[1].section == Section(start=1800.0)
    assert states[1].part_of == PartOrigin(id="BVlong1", parts=2)
    assert states[1].origin_source == SOURCE
    assert states[1].translation_hint == (
        "節目資訊\nThis is part 2 of 2 of one continuous video, so it may start "
        "and end mid-conversation."
    )
    assert states[0].parent is None
    assert states[1].parent == first.root.resolve()
    for layout in (first, second):
        assert layout.video.is_file()
        assert layout.poster.read_bytes() == b"poster"
        assert not layout.full_video.exists()
    # The last part cuts to the end, the first from the start.
    first_cut, second_cut = cuts(ffmpeg)
    assert "-ss" not in first_cut
    assert second_cut[second_cut.index("-ss") + 1] == "1800.0"
    assert "-t" not in second_cut


def test_captions_are_shifted_onto_each_part(loaded: LoadedConfig, run_split: RunSplit):
    run_split()

    first, second = part_layouts(loaded)
    assert [block.text for block in read_srt_file(first.ja_official_srt)] == ["early"]
    (late,) = read_srt_file(second.ja_official_srt)
    assert late.text == "late"
    assert late.timecode.startswith("00:10:00,000")


def test_a_re_run_resumes_without_download_or_cut(
    loaded: LoadedConfig, journal: Journal, run_split: RunSplit
):
    on_asr, failures = failing_on("BVlong1p2")
    ffmpeg = FakeFfmpeg(duration=3600.0)
    with pytest.raises(StageFailedError):
        run_split(ffmpeg, on_asr=on_asr, hint="節目資訊")
    assert journal.entries.count("run:download@download") == 1
    failures.clear()
    first, second = part_layouts(loaded)
    calls = len(ffmpeg.calls)

    final = run_split(ffmpeg, on_asr=on_asr, hint="另一個提示")

    assert final == second
    assert len(ffmpeg.calls) == calls
    assert journal.entries.count("run:download@download") == 1
    assert load_state(second).parent == first.root.resolve()
    assert (load_state(second).translation_hint or "").startswith("節目資訊")


def test_the_dashboard_retry_continues_the_chain(
    loaded: LoadedConfig, journal: Journal, recording_sink: RecordingSink
):
    on_asr, failures = failing_on("BVlong1p1")
    run = split(FakeFfmpeg(duration=3600.0))
    stages = pipeline(journal, on_asr)
    with pytest.raises(StageFailedError):
        run.run(loaded, sinks=[recording_sink], pipeline=stages)
    failures.clear()

    final = run.run(loaded, sinks=[recording_sink], pipeline=stages)

    assert final == part_layouts(loaded)[1]


class _FailsOnSecondCut(FakeFfmpeg):
    def run(self, argv: Sequence[str], **kwargs: object) -> str:
        if argv[0] != "ffprobe" and cuts(self):
            raise StageFailedError("disk full")
        return super().run(argv, **kwargs)  # pyright: ignore[reportArgumentType]


def test_an_interrupted_split_keeps_the_source_and_cuts_only_the_rest(
    loaded: LoadedConfig, journal: Journal, run_split: RunSplit
):
    with pytest.raises(StageFailedError, match="disk full"):
        run_split(_FailsOnSecondCut(duration=3600.0))

    first, second = part_layouts(loaded)
    source = ProjectLayout.for_id(loaded.projects_root, "BVlong1")
    assert first.project_json.is_file()
    assert not second.project_json.exists()
    assert source.full_video.is_file()

    retry = FakeFfmpeg(duration=3600.0)
    run_split(retry)

    assert len(cuts(retry)) == 1
    assert journal.entries.count("run:download@download") == 1
    assert load_state(second).section == Section(start=1800.0)
    assert not source.root.exists()


def test_unusable_captions_fail_before_any_cut(
    loaded: LoadedConfig, recording_sink: RecordingSink
):
    journal = Journal()

    def download(ctx: StageContext) -> None:
        ctx.layout.full_video.parent.mkdir(parents=True, exist_ok=True)
        ctx.layout.full_video.write_bytes(b"video")
        ctx.layout.download_parts_dir.mkdir(parents=True, exist_ok=True)
        (ctx.layout.download_parts_dir / "0.ja.srt").write_text("", encoding="utf-8")

    ffmpeg = FakeFfmpeg(duration=3600.0)
    stages = Pipeline(
        (
            fake_stage(StageKey.METADATA, journal),
            fake_stage(StageKey.DOWNLOAD, journal, action=download),
        )
    )
    with pytest.raises(SourceError, match="no subtitles"):
        split(ffmpeg).run(loaded, sinks=[recording_sink], pipeline=stages)

    assert cuts(ffmpeg) == []


@pytest.mark.parametrize("parts", [2, 4])
def test_a_different_part_count_is_refused(run_split: RunSplit, parts: int):
    run_split(parts=3)
    with pytest.raises(SplitError, match=f"{parts}-part"):
        run_split(parts=parts)


def test_an_unrelated_project_with_a_part_id_is_refused(
    loaded: LoadedConfig, run_split: RunSplit
):
    create_project(loaded.projects_root, SourceId(Platform.BILIBILI, "BVlong1p1"))
    with pytest.raises(SplitError, match="2-part"):
        run_split()


def test_a_project_past_the_download_is_refused(
    loaded: LoadedConfig, run_split: RunSplit
):
    layout, state = create_project(loaded.projects_root, SOURCE)
    for key in (StageKey.METADATA, StageKey.DOWNLOAD, StageKey.COMBINE):
        state.mark_done(key, elapsed_s=1.0)
    save_state(layout, state)

    with pytest.raises(SplitError, match="--from combine"):
        run_split()
    assert layout.project_json.is_file()


@pytest.mark.parametrize(
    ("parts", "options", "message"),
    [
        (1, {}, "at least 2"),
        (2, {"break_after": StageKey.ASR}, "--break-after"),
        (2, {"section": Section(start=1.0)}, "--start/--to"),
    ],
)
def test_impossible_requests_fail_before_any_work(
    parts: int, options: dict[str, object], message: str
):
    with pytest.raises(ValueError, match=message):
        split(FakeFfmpeg(), parts=parts, **options)


def test_the_parent_seeds_the_first_part(
    loaded: LoadedConfig, run_split: RunSplit, tmp_path: Path
):
    earlier, _ = create_project(
        tmp_path / "archive", SourceId(Platform.TVER, "epearlier")
    )
    run_split(parent=earlier.root)
    assert load_state(part_layouts(loaded)[0]).parent == earlier.root.resolve()


def test_a_run_the_parts_could_not_finish_fails_before_the_download(
    loaded: LoadedConfig, journal: Journal, recording_sink: RecordingSink
):
    def missing_key(*_: object) -> None:
        raise ValueError("no ASR key")

    metadata, download, combine, asr = pipeline(journal).stages
    stages = Pipeline(
        (metadata, download, combine, replace(asr, preflight=missing_key))
    )
    ffmpeg = FakeFfmpeg(duration=3600.0)

    with pytest.raises(ValueError, match="no ASR key"):
        split(ffmpeg).run(loaded, sinks=[recording_sink], pipeline=stages)

    assert journal.entries == []
    assert ffmpeg.calls == []
    assert not ProjectLayout.for_id(loaded.projects_root, "BVlong1").root.exists()


def test_a_source_that_went_on_survives_a_re_run(
    loaded: LoadedConfig, run_split: RunSplit
):
    run_split()
    layout, state = create_project(loaded.projects_root, SOURCE)
    for key in (StageKey.METADATA, StageKey.DOWNLOAD, StageKey.COMBINE):
        state.mark_done(key, elapsed_s=1.0)
    save_state(layout, state)

    with pytest.raises(SplitError, match="--from combine"):
        run_split()

    assert layout.project_json.is_file()


@pytest.mark.parametrize(
    "stage", [StageKey.METADATA, StageKey.DOWNLOAD, StageKey.COMBINE]
)
def test_a_part_refuses_a_reset_of_what_its_split_seeded(
    loaded: LoadedConfig, run_split: RunSplit, stage: StageKey
):
    run_split()
    part = part_layouts(loaded)[1]
    before = load_state(part)

    with pytest.raises(ValueError, match="cannot run again"):
        reset(part, before, stages_from(stage))

    assert part.video.is_file()
    assert load_state(part).section == Section(start=1800.0)


def test_a_part_can_reset_a_later_stage(loaded: LoadedConfig, run_split: RunSplit):
    run_split()
    part = part_layouts(loaded)[1]

    reset(part, load_state(part), stages_from(StageKey.ASR))

    state = load_state(part)
    assert not state.is_done(StageKey.ASR)
    assert state.is_done(StageKey.COMBINE)
    assert part.video.is_file()
