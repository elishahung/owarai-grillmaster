"""Temporary parity check of the package recipe against the legacy media module.

The package look, encode recipe, part split and the mux/concat argvs were moved
verbatim from `grillmaster.legacy.services.media.MediaProcessor`; this module
pins them to the legacy values until the legacy package is deleted.

Delete with legacy: remove this file together with `src/grillmaster/legacy/`.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest
from tests.fakes import FakeFfmpeg

from grillmaster.core.timecode import TimeRange
from grillmaster.legacy.services import media as legacy_media
from grillmaster.legacy.services.media import MediaProcessor
from grillmaster.legacy.services.package import constants as legacy_package
from grillmaster.package import remix, render
from grillmaster.package.render import BurnPlan, SubtitledRange

if TYPE_CHECKING:
    from pathlib import Path

FFMPEG_PREFIX = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]

SHARED_CONSTANTS = [
    "BURN_IN_DURATION_TOLERANCE_SECONDS",
    "PACKAGE_TEMPO",
    "PACKAGE_PITCH",
    "PACKAGE_NOISE_AMPLITUDE",
    "PACKAGE_LEAD_TRIM_SECONDS",
    "PACKAGE_SEEK_MARGIN_SECONDS",
    "PACKAGE_ROTATE_DEGREES",
    "PACKAGE_ROTATE_RADIANS",
    "PACKAGE_OUTPUT_FPS",
    "PACKAGE_FRAME_WIDTH",
    "PACKAGE_FRAME_HEIGHT",
    "PACKAGE_ENCODE_CONCURRENCY",
    "PACKAGE_MIN_PART_SECONDS",
    "PACKAGE_VIDEO_CQ",
    "PACKAGE_VIDEO_MAXRATE",
    "PACKAGE_VIDEO_BUFSIZE",
]


@pytest.mark.parametrize("name", SHARED_CONSTANTS)
def test_package_constants_match_legacy(name: str):
    assert getattr(render, name) == getattr(legacy_media, name)


def test_filters_and_encode_args_match_legacy():
    legacy = MediaProcessor
    assert render._PACKAGE_VIDEO_FILTER == legacy._PACKAGE_VIDEO_FILTER
    assert render._PACKAGE_VIDEO_OUTPUT == legacy._PACKAGE_VIDEO_OUTPUT
    assert render._PACKAGE_AUDIO_FILTER == legacy._PACKAGE_AUDIO_FILTER
    assert list(render.PACKAGE_ENCODE_ARGS) == legacy._PACKAGE_ENCODE_ARGS
    assert remix._NOISE_VIDEO_FILTER == legacy._NOISE_VIDEO_FILTER
    assert remix._NOISE_AUDIO_FILTER == legacy._NOISE_AUDIO_FILTER


def test_remix_constants_match_legacy():
    assert remix.NOISE_CUT_SECONDS == legacy_package.NOISE_CUT_DURATION_SECONDS
    assert remix.REMIX_TARGET_SEGMENT_SECONDS == (
        legacy_package.REMIX_TARGET_SEGMENT_SECONDS
    )
    assert remix.REMIX_MIN_SEGMENT_COUNT == legacy_package.REMIX_MIN_SEGMENT_COUNT
    assert remix.REMIX_MIN_SEGMENT_SECONDS == legacy_package.REMIX_MIN_SEGMENT_SECONDS


@pytest.mark.parametrize(("start", "duration"), [(3.0, 997.0), (501.5, 498.5)])
def test_audio_graph_and_seek_match_legacy(start: float, duration: float):
    legacy = MediaProcessor
    assert render.package_audio_graph(start, duration) == (
        legacy._package_audio_graph(start, duration)
    )
    assert render.package_seek_args(start, duration) == (
        legacy._package_seek_args(start, duration)
    )
    assert render.noise_bed_mix(duration) == legacy._noise_bed_mix(duration)


@pytest.mark.parametrize("usable", [100.0, 239.0, 240.0, 997.0, 7200.0])
def test_burn_in_parts_match_legacy(usable: float):
    lead = render.PACKAGE_LEAD_TRIM_SECONDS
    parts = render.split_range(
        TimeRange(lead, lead + usable), render.render_part_count(usable)
    )
    legacy = MediaProcessor.burn_in_parts(usable)
    assert [(part.start, part.end) for part in parts] == [
        (part.start_seconds, part.end_seconds) for part in legacy
    ]


def test_mux_argv_golden(tmp_path: Path):
    project = tmp_path / "project"
    (project / "subs").mkdir(parents=True)
    scratch = tmp_path / "scratch"
    fake = FakeFfmpeg()
    output = tmp_path / "video.mp4"
    subtitled = SubtitledRange(
        fake,
        project / "video.mp4",
        BurnPlan.dialogue(project / "subs" / "cht.ass"),
        (TimeRange(3.0, 100.0), TimeRange(100.0, 200.0)),
        scratch,
    )

    subtitled.mux(output, threading.Event())

    (argv,) = fake.calls
    concat_list = argv[argv.index("-i") + 1]
    # Legacy: the same arguments after its `-progress pipe:1` flags, with
    # `-y` at the end instead of in the prefix.
    assert argv == [
        *FFMPEG_PREFIX,
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        concat_list,
        "-i",
        str(scratch / "audio.m4a"),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output),
    ]


def test_remix_concat_argv_golden(tmp_path: Path):
    fake = FakeFfmpeg()
    output = tmp_path / "1.mp4"

    remix.concat_remix_segments(
        fake, [tmp_path / "head.mp4", tmp_path / "target.mp4"], output
    )

    (argv,) = fake.calls
    concat_list = argv[argv.index("-i") + 1]
    assert argv == [
        *FFMPEG_PREFIX,
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        concat_list,
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-af",
        "aresample=async=1:first_pts=0",
        "-avoid_negative_ts",
        "make_zero",
        "-movflags",
        "+faststart",
        str(output),
    ]
