from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, override

import pytest
from tests.fakes import RecordingSink
from tests.package.conftest import PackageFfmpeg, arg_after, encodes

from grillmaster.core.timecode import TimeRange
from grillmaster.events.types import ProgressAdvanced, ProgressStarted
from grillmaster.media.errors import MediaError
from grillmaster.package.errors import PackageError
from grillmaster.package.pools import CURSOR_FILE_NAME, MediaPool
from grillmaster.package.remix import (
    RemixPlan,
    plan_remix,
    remix_segment_count,
    render_remix,
    select_remix_segments,
)
from grillmaster.package.render import (
    PACKAGE_ENCODE_ARGS,
    BurnPlan,
    package_audio_graph,
    package_output_duration,
    split_range,
)

if TYPE_CHECKING:
    import threading
    from collections.abc import Sequence

    from grillmaster.media.ffmpeg import ProgressCallback


def spans(*pairs: tuple[float, float]) -> list[TimeRange]:
    return [TimeRange(start, end) for start, end in pairs]


def bounds(segments: list[TimeRange]) -> list[tuple[float, float]]:
    return [(segment.start, segment.end) for segment in segments]


# --- segment selection (ported) -----------------------------------------------


def test_short_video_stays_one_piece():
    segments = select_remix_segments(spans((0, 10), (70, 80)), 100.0)
    assert bounds(segments) == [(0.0, 100.0)]


def test_short_video_splits_in_half():
    segments = select_remix_segments(spans((0, 590), (610, 1200)), 1200.0)
    assert bounds(segments) == [(0.0, 600.0), (600.0, 1200.0)]


def test_falls_back_to_the_nearest_boundary():
    segments = select_remix_segments(spans((0, 400), (400, 1000)), 1000.0)
    assert bounds(segments) == [(0.0, 400.0), (400.0, 1000.0)]


def test_splits_into_equal_parts():
    segments = select_remix_segments(spans((0, 890), (910, 1790), (1810, 2700)), 2700.0)
    assert bounds(segments) == [(0.0, 900.0), (900.0, 1800.0), (1800.0, 2700.0)]


def test_rebalances_after_a_snapped_cut():
    segments = select_remix_segments(spans((0, 950), (960, 1820), (1830, 2700)), 2700.0)
    # The first cut snaps late to 950 s, so the second targets the middle of
    # the remaining 1750 s (1825 s), not the fixed 2/3 mark (1800 s).
    assert bounds(segments) == [(0.0, 950.0), (950.0, 1825.0), (1825.0, 2700.0)]


def test_splits_from_the_start_offset():
    segments = select_remix_segments(
        spans((0, 590), (610, 1200)), 1200.0, start_seconds=3.0
    )
    assert bounds(segments) == [(3.0, 601.5), (601.5, 1200.0)]


def test_ranges_need_not_be_sorted():
    segments = select_remix_segments(spans((610, 1200), (0, 590)), 1200.0)
    assert bounds(segments) == [(0.0, 600.0), (600.0, 1200.0)]


def test_rejects_a_start_past_the_end():
    with pytest.raises(PackageError):
        select_remix_segments(spans((0, 1)), 3.0, start_seconds=3.0)


def test_rejects_no_subtitles():
    with pytest.raises(PackageError):
        select_remix_segments([], 100.0)


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(5, 2), (20, 2), (37, 2), (37.5, 3), (38, 3), (45, 3), (52, 3), (53, 4), (60, 4)],
)
def test_segment_count_rounds_fifteen_minute_parts(minutes: float, expected: int):
    assert remix_segment_count(minutes * 60.0) == expected


# --- render_remix ---------------------------------------------------------------


@pytest.fixture
def noise(tmp_path: Path) -> MediaPool:
    directory = tmp_path / "package" / "pools" / "sleep"
    directory.mkdir(parents=True)
    for index in range(1, 5):
        (directory / f"{index:03d}.mp4").write_text("noise", encoding="utf-8")
    return MediaPool(directory)


SUBTITLES = spans((0, 490), (510, 1000))
# What `plan_remix` makes of SUBTITLES over a 1000 s video.
PLAN = RemixPlan(tuple(spans((3.0, 501.5), (501.5, 1000.0))))
PART_SECONDS = package_output_duration(498.5)


def test_plan_splits_after_the_lead_trim(project: Path):
    fake = PackageFfmpeg({project / "video.mp4": 1000.0})

    assert plan_remix(fake, video=project / "video.mp4", subtitles=SUBTITLES) == PLAN


def test_plan_refuses_a_video_inside_the_lead_trim(project: Path):
    fake = PackageFfmpeg({project / "video.mp4": 2.0})

    with pytest.raises(PackageError):
        plan_remix(fake, video=project / "video.mp4", subtitles=SUBTITLES)


def test_renders_noise_headed_parts_and_reserves_the_noise(
    tmp_path: Path, project: Path, noise: MediaPool
):
    target = tmp_path / "deliverable"
    target.mkdir()
    fake = PackageFfmpeg(
        {
            project / "video.mp4": 1000.0,
            noise.directory / "001.mp4": 150.0,
            target / "1.mp4": 60.0 + PART_SECONDS,
            target / "2.mp4": 90.0 + PART_SECONDS,
        }
    )
    sink = RecordingSink()

    outputs = render_remix(
        fake,
        video=project / "video.mp4",
        burn=BurnPlan.dialogue(project / "video.cht.ass"),
        plan=PLAN,
        noise=noise,
        target_dir=target,
        events=sink,
    )

    assert outputs == [target / "1.mp4", target / "2.mp4"]
    assert all(output.exists() for output in outputs)
    assert not (target / "3.mp4").exists()
    calls = encodes(fake)
    noise_cuts = sorted(
        (arg_after(argv, "-ss"), arg_after(argv, "-t"))
        for argv in calls
        if "-af" in argv and "-vf" in argv
    )
    assert noise_cuts == [("0.000", "60.000"), ("60.000", "90.000")]
    # Each segment's content renders as parts sharing the NVENC sessions,
    # with one audio pass per segment.
    trims = sorted(
        arg_after(argv, "-vf").split(",trim=")[1].split(",")[0]
        for argv in calls
        if "-vf" in argv and "-af" not in argv
    )
    parts = [part for segment in PLAN.segments for part in split_range(segment, 3)]
    assert len(parts) == 6
    assert trims == sorted(
        f"start={part.start:.3f}:duration={part.duration:.3f}" for part in parts
    )
    audio_graphs = sorted(
        arg_after(argv, "-filter_complex")
        for argv in calls
        if "-filter_complex" in argv
    )
    assert audio_graphs == sorted(
        package_audio_graph(segment.start, segment.duration)
        for segment in PLAN.segments
    )
    noise_argv = next(argv for argv in calls if "-af" in argv and "-vf" in argv)
    assert arg_after(noise_argv, "-vf") == (
        "scale=1920:1080:flags=bicubic,format=yuv420p,fps=29.94"
    )
    assert arg_after(noise_argv, "-af") == (
        "aformat=sample_rates=44100:channel_layouts=stereo"
    )
    assert noise_argv[-len(PACKAGE_ENCODE_ARGS) - 1 : -1] == list(PACKAGE_ENCODE_ARGS)
    concats = [argv for argv in calls if "aresample=async=1:first_pts=0" in argv]
    assert sorted(argv[-1] for argv in concats) == [str(o) for o in outputs]
    cursor = json.loads((noise.directory / CURSOR_FILE_NAME).read_text("utf-8"))
    assert cursor == {"index": 1, "seconds": 0}

    total = 2 * PART_SECONDS + 150.0
    started = sink.events[0]
    assert isinstance(started, ProgressStarted)
    assert (started.scope, started.label) == ("package:remix", "Remixing subtitles")
    assert started.total == pytest.approx(total)
    advanced = sum(e.n for e in sink.events if isinstance(e, ProgressAdvanced))
    assert advanced == pytest.approx(total)


def test_a_failed_render_still_consumes_the_reserved_noise(
    tmp_path: Path, project: Path, noise: MediaPool
):
    failing = PackageFfmpeg(
        {project / "video.mp4": 1000.0, noise.directory / "001.mp4": 600.0}
    )
    failing.fail_encodes = MediaError("nvenc out of sessions")
    target = tmp_path / "deliverable"
    target.mkdir()

    with pytest.raises(MediaError):
        render_remix(
            failing,
            video=project / "video.mp4",
            burn=BurnPlan.dialogue(project / "video.cht.ass"),
            plan=PLAN,
            noise=noise,
            target_dir=target,
            events=RecordingSink(),
        )

    # The cursor is a reservation: a concurrent run must never draw the same
    # noise, so a failed render does not give it back.
    cursor = json.loads((noise.directory / CURSOR_FILE_NAME).read_text("utf-8"))
    assert cursor == {"index": 0, "seconds": 120}


def test_a_truncated_part_fails_the_remix(
    tmp_path: Path, project: Path, noise: MediaPool
):
    target = tmp_path / "deliverable"
    target.mkdir()
    # ffmpeg exited 0, but part 2 stops 30 s short (an SMB drop mid-write).
    fake = PackageFfmpeg(
        {
            project / "video.mp4": 1000.0,
            noise.directory / "001.mp4": 150.0,
            target / "1.mp4": 60.0 + PART_SECONDS,
            target / "2.mp4": 60.0 + PART_SECONDS,
        }
    )

    with pytest.raises(PackageError, match=r"2\.mp4"):
        render_remix(
            fake,
            video=project / "video.mp4",
            burn=BurnPlan.dialogue(project / "video.cht.ass"),
            plan=PLAN,
            noise=noise,
            target_dir=target,
            events=RecordingSink(),
        )


def test_every_part_shares_one_abort_event(
    tmp_path: Path, project: Path, noise: MediaPool
):
    target = tmp_path / "deliverable"
    target.mkdir()
    fake = PackageFfmpeg(
        {
            project / "video.mp4": 1000.0,
            noise.directory / "001.mp4": 150.0,
            target / "1.mp4": 60.0 + PART_SECONDS,
            target / "2.mp4": 90.0 + PART_SECONDS,
        }
    )

    render_remix(
        fake,
        video=project / "video.mp4",
        burn=BurnPlan.dialogue(project / "video.cht.ass"),
        plan=PLAN,
        noise=noise,
        target_dir=target,
        events=RecordingSink(),
    )

    # Noise, three video parts, audio, mux and concat per segment: one
    # failure anywhere must be able to stop all of them.
    events = {id(event) for event in fake.aborts}
    assert len(fake.aborts) == 14
    assert len(events) == 1
    assert None not in fake.aborts


class ScratchWatch(PackageFfmpeg):
    """Holds segment 2's final concat until segment 1's scratch directory
    (found from its noise head's output) is gone, recording whether it went."""

    def __init__(self, durations: dict[Path, float], second: Path) -> None:
        super().__init__(durations)
        self.second = second
        self.first_scratch_gone: bool | None = None

    @override
    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        cwd: Path | None = None,
        on_progress: ProgressCallback | None = None,
        abort: threading.Event | None = None,
    ) -> str:
        if argv[0] == "ffmpeg" and argv[-1] == str(self.second):
            heads = [
                Path(call[-1])
                for call in encodes(self)
                if call[-1].endswith("head.mp4")
            ]
            first = next(head.parent for head in heads if head.parent.name == "1")
            deadline = time.monotonic() + 5
            while first.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.first_scratch_gone = not first.exists()
        return super().run(
            argv, timeout=timeout, cwd=cwd, on_progress=on_progress, abort=abort
        )


def test_a_finished_segment_deletes_its_scratch_at_once(
    tmp_path: Path, project: Path, noise: MediaPool
):
    target = tmp_path / "deliverable"
    target.mkdir()
    fake = ScratchWatch(
        {
            project / "video.mp4": 1000.0,
            noise.directory / "001.mp4": 150.0,
            target / "1.mp4": 60.0 + PART_SECONDS,
            target / "2.mp4": 90.0 + PART_SECONDS,
        },
        second=target / "2.mp4",
    )

    render_remix(
        fake,
        video=project / "video.mp4",
        burn=BurnPlan.dialogue(project / "video.cht.ass"),
        plan=PLAN,
        noise=noise,
        target_dir=target,
        events=RecordingSink(),
    )

    # Segment 1's parts, head and target went while segment 2 still worked.
    assert fake.first_scratch_gone is True
