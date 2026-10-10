from __future__ import annotations

import itertools
import threading
from typing import TYPE_CHECKING

import pytest
from tests.fakes import RecordingSink
from tests.package.conftest import PackageFfmpeg, arg_after, encodes

from grillmaster.events.types import ProgressAdvanced, ProgressFinished, ProgressStarted
from grillmaster.live_chat.render import PictureBox
from grillmaster.media.errors import MediaError
from grillmaster.package.errors import PackageError, RenderAbortedError
from grillmaster.package.render import (
    PACKAGE_ENCODE_ARGS,
    PACKAGE_ENCODE_CONCURRENCY,
    PACKAGE_LEAD_TRIM_SECONDS,
    PACKAGE_MIN_PART_SECONDS,
    PACKAGE_OUTPUT_FPS,
    PACKAGE_TEMPO,
    BurnPlan,
    SubtitleLayer,
    burn_in,
    burn_in_parts,
    package_audio_graph,
    package_output_duration,
    package_seek_args,
    package_usable_duration,
    package_video_chain,
    run_in_pool,
)

if TYPE_CHECKING:
    from pathlib import Path

# Golden strings: the package recipe is measured and must not drift. The
# look part is byte-identical to the legacy media module's.
LOOK = (
    "scale=1960:1102:flags=bicubic,"
    "rotate=a=0.003490658503988659:ow=rotw(0.003490658503988659):"
    "oh=roth(0.003490658503988659):c=black:bilinear=0,"
    "crop=1920:1080,eq=brightness=0.02:contrast=1.03:saturation=1.05,"
    "hue=h=4,noise=c0s=4:c0f=t+u"
)
OUTPUT = "setpts=PTS-STARTPTS,setpts=PTS/1.03,format=yuv420p,fps=29.94"
TRIM = "trim=start=3.000:duration=10.000"
SIDE_PLAN_LAYERS = (
    "subtitles=work/package/chat.ass,"
    "subtitles=subs/cht.ass:force_style='MarginR=394,MarginV=24'"
)


def side_plan(root: Path) -> BurnPlan:
    return BurnPlan(
        layers=(
            SubtitleLayer(root / "work" / "package" / "chat.ass"),
            SubtitleLayer(root / "subs" / "cht.ass", "MarginR=394,MarginV=24"),
        ),
        picture=PictureBox(0, 108, 1536, 864),
    )


def test_dialogue_video_chain(project: Path):
    plan = BurnPlan.dialogue(project / "subs" / "cht.ass")
    assert package_video_chain(plan, TRIM, project) == (
        f"{LOOK},subtitles=subs/cht.ass,{TRIM},{OUTPUT}"
    )


def test_canvas_sits_between_the_look_and_the_layers(project: Path):
    assert package_video_chain(side_plan(project), TRIM, project) == (
        f"{LOOK},scale=1536:864,pad=1920:1080:0:108:black,"
        f"{SIDE_PLAN_LAYERS},{TRIM},{OUTPUT}"
    )


def test_audio_graph_and_seek():
    assert package_audio_graph(3.0, 103.0) == (
        "[0:a]atrim=start=3.000:duration=103.000,asetpts=PTS-STARTPTS,"
        "highpass=f=50,lowpass=f=15000,rubberband=tempo=1.03:pitch=1.01,"
        "aformat=sample_rates=44100:channel_layouts=stereo,volume=0.97[a0];"
        "anoisesrc=d=100.000:s=44100:a=0.002:c=pink,"
        "aformat=sample_rates=44100:channel_layouts=stereo[an];"
        "[a0][an]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
    )
    assert package_seek_args(3.0, 100.0) == [
        "-copyts",
        "-start_at_zero",
        "-ss",
        "1.000",
        "-t",
        "104.000",
    ]
    assert package_seek_args(600.0, 100.0)[3] == "598.000"


def test_encode_args_keep_the_nvenc_recipe():
    assert PACKAGE_ENCODE_ARGS == (
        "-c:v", "h264_nvenc", "-preset", "p4", "-tune", "hq", "-rc", "vbr",
        "-cq", "21", "-maxrate", "24000k", "-bufsize", "12000k",
        "-profile:v", "high", "-spatial-aq", "1",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart",
    )  # fmt: skip


def test_dialogue_plan_has_no_canvas(project: Path):
    assert BurnPlan.dialogue(project / "subs" / "cht.ass").canvas_filter is None


@pytest.mark.parametrize(
    "box",
    [
        pytest.param(PictureBox(0, 108, 1922, 864), id="too-wide"),
        pytest.param(PictureBox(-2, 0, 100, 100), id="negative"),
        pytest.param(PictureBox(1, 108, 1536, 864), id="odd-offset"),
        pytest.param(PictureBox(0, 108, 1536, 863), id="odd-size"),
    ],
)
def test_picture_box_must_fit_the_frame_on_even_pixels(project: Path, box: PictureBox):
    with pytest.raises(ValueError, match="picture box"):
        BurnPlan(layers=(SubtitleLayer(project / "a.ass"),), picture=box)


def test_plan_needs_a_layer():
    with pytest.raises(ValueError, match="at least one"):
        BurnPlan(layers=())


def test_usable_duration_rejects_a_video_inside_the_lead_trim():
    with pytest.raises(PackageError):
        package_usable_duration(PACKAGE_LEAD_TRIM_SECONDS)


# --- burn_in_parts (ported) ---------------------------------------------------


def test_short_video_stays_one_part():
    parts = burn_in_parts(PACKAGE_MIN_PART_SECONDS * 2 - 1)
    assert len(parts) == 1
    assert parts[0].start == PACKAGE_LEAD_TRIM_SECONDS


def test_part_count_is_capped_by_the_minimum_part_length():
    assert len(burn_in_parts(PACKAGE_MIN_PART_SECONDS * 2)) == 2


def test_long_video_uses_the_whole_encode_pool():
    assert len(burn_in_parts(7200.0)) == PACKAGE_ENCODE_CONCURRENCY


def test_parts_tile_the_trimmed_range_without_gaps():
    usable = 7200.0
    parts = burn_in_parts(usable)
    assert parts[0].start == PACKAGE_LEAD_TRIM_SECONDS
    assert parts[-1].end == pytest.approx(PACKAGE_LEAD_TRIM_SECONDS + usable)
    for earlier, later in itertools.pairwise(parts):
        assert earlier.end == later.start


def test_boundaries_land_on_whole_output_frames():
    # Only the last part carries the sub-frame remainder; every boundary
    # before it must sit on a frame, or the concatenated parts would not add
    # up to the frame count a single pass produces.
    parts = burn_in_parts(7200.0)
    for part in parts[:-1]:
        frames = package_output_duration(part.duration) * PACKAGE_OUTPUT_FPS
        assert frames == pytest.approx(round(frames), abs=1e-6)
    total = sum(package_output_duration(part.duration) for part in parts)
    assert total == pytest.approx(package_output_duration(7200.0))
    first = parts[0].duration
    frames_in_first = round(package_output_duration(first) * PACKAGE_OUTPUT_FPS)
    assert first / frames_in_first == pytest.approx(PACKAGE_TEMPO / PACKAGE_OUTPUT_FPS)


# --- burn_in ------------------------------------------------------------------


def burned(project: Path, output: Path, *, source: float = 1000.0) -> PackageFfmpeg:
    """A fake whose output probes at the expected sped-up length."""
    expected = package_output_duration(source - PACKAGE_LEAD_TRIM_SECONDS)
    return PackageFfmpeg({project / "video.mp4": source, output: expected})


def test_burn_in_renders_parts_and_audio_then_muxes(tmp_path: Path, project: Path):
    output = tmp_path / "out" / "video.mp4"
    fake = burned(project, output)
    sink = RecordingSink()

    burn_in(
        fake,
        project / "video.mp4",
        BurnPlan.dialogue(project / "subs" / "cht.ass"),
        output,
        events=sink,
    )

    *renders, mux = encodes(fake)
    video_parts = [argv for argv in renders if "-vf" in argv]
    audio = [argv for argv in renders if "-filter_complex" in argv]
    assert len(video_parts) == 3
    assert len(audio) == 1
    for argv in video_parts:
        # Bare name: the render runs in the video's directory.
        assert arg_after(argv, "-i") == "video.mp4"
        assert arg_after(argv, "-map") == "0:v:0"
        assert "-an" in argv
        assert arg_after(argv, "-vf").startswith(f"{LOOK},subtitles=subs/cht.ass,")
        assert argv[-len(PACKAGE_ENCODE_ARGS) - 1 : -1] == list(PACKAGE_ENCODE_ARGS)
    assert arg_after(audio[0], "-map") == "[a]"
    assert arg_after(audio[0], "-filter_complex") == package_audio_graph(3.0, 997.0)
    assert mux[-1] == str(output)
    assert mux[mux.index("-map") : mux.index("-map") + 4] == [
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
    ]
    assert fake.cwds.count(project) == 3
    assert output.exists()

    started, *advances, finished = sink.events
    assert started == ProgressStarted(
        "package:burn-in", "Burning subtitles", package_output_duration(997.0)
    )
    assert finished == ProgressFinished("package:burn-in")
    assert all(isinstance(event, ProgressAdvanced) for event in advances)
    assert sum(
        event.n for event in advances if isinstance(event, ProgressAdvanced)
    ) == pytest.approx(package_output_duration(997.0))


def test_progress_is_capped_per_process(tmp_path: Path, project: Path):
    output = tmp_path / "video.mp4"
    fake = burned(project, output, source=100.0)
    fake.report_seconds = 1_000_000.0
    sink = RecordingSink()

    burn_in(
        fake,
        project / "video.mp4",
        BurnPlan.dialogue(project / "subs" / "cht.ass"),
        output,
        events=sink,
    )

    advances = [event for event in sink.events if isinstance(event, ProgressAdvanced)]
    assert sum(event.n for event in advances) == pytest.approx(
        package_output_duration(97.0)
    )


def test_burn_in_rejects_an_output_that_was_not_sped_up(tmp_path: Path, project: Path):
    output = tmp_path / "video.mp4"
    fake = PackageFfmpeg({project / "video.mp4": 1000.0, output: 997.0})
    sink = RecordingSink()

    with pytest.raises(PackageError, match="duration differs"):
        burn_in(
            fake,
            project / "video.mp4",
            BurnPlan.dialogue(project / "subs" / "cht.ass"),
            output,
            events=sink,
        )
    assert sink.events[-1] == ProgressFinished("package:burn-in")


def test_burn_in_refuses_subtitles_outside_the_video_directory(
    tmp_path: Path, project: Path
):
    elsewhere = tmp_path / "elsewhere.ass"
    with pytest.raises(PackageError, match="under the video's directory"):
        burn_in(
            PackageFfmpeg(),
            project / "video.mp4",
            BurnPlan.dialogue(elsewhere),
            tmp_path / "out.mp4",
            events=RecordingSink(),
        )


def test_burn_in_hands_one_abort_event_to_every_run(tmp_path: Path, project: Path):
    output = tmp_path / "video.mp4"
    fake = burned(project, output)

    burn_in(
        fake,
        project / "video.mp4",
        BurnPlan.dialogue(project / "subs" / "cht.ass"),
        output,
        events=RecordingSink(),
    )

    # Three video parts, the audio and the mux.
    assert len(fake.aborts) == 5
    assert len({id(event) for event in fake.aborts}) == 1
    assert None not in fake.aborts


# --- run_in_pool ---------------------------------------------------------------


def test_pool_returns_results_in_job_order():
    jobs = [lambda _abort, value=value: value for value in range(5)]
    assert run_in_pool(jobs, max_workers=2) == [0, 1, 2, 3, 4]


def test_first_failure_aborts_running_jobs_and_cancels_queued_ones():
    started = threading.Event()
    ran: list[str] = []

    def fail(_abort: threading.Event) -> None:
        started.wait(5)
        raise MediaError("nvenc out of sessions")

    def long_render(abort: threading.Event) -> None:
        started.set()
        # A real render's ffmpeg is killed by the runner when `abort` is set.
        if not abort.wait(5):
            ran.append("render finished unaborted")
        raise MediaError("ffmpeg aborted")

    def queued(_abort: threading.Event) -> None:
        ran.append("queued job ran")

    with pytest.raises(MediaError, match="nvenc"):
        run_in_pool([fail, long_render, queued], max_workers=2)
    assert ran == []


def test_a_nested_pool_shares_the_callers_abort_event():
    outer = threading.Event()
    seen: list[threading.Event] = []

    run_in_pool([seen.append, seen.append], max_workers=2, abort=outer)

    assert seen == [outer, outer]


def test_an_aborted_pool_starts_no_job():
    abort = threading.Event()
    abort.set()
    ran: list[int] = []

    with pytest.raises(RenderAbortedError):
        run_in_pool([lambda _abort: ran.append(1)], max_workers=1, abort=abort)
    assert ran == []
