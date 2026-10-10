"""Remix packaging: the show split into parts, each headed by a noise cut.

The range after the package lead trim splits into `round_half_up(duration /
15 min)` parts (at least two) whose cuts snap to subtitle gaps or
boundaries; each part renders as a noise cut from a media pool followed by
the subtitled content (`1.mp4`, `2.mp4`, ...). Noise is transcoded at remix
time with a format-only fit (no look filters) so the concat can stream-copy
the video. `plan_remix` picks the segments before anything is drawn from a
pool; `render_remix` then reserves the noise before anything renders.
"""

from __future__ import annotations

import itertools
import math
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.timecode import TimeRange
from grillmaster.media import probe
from grillmaster.media.ffmpeg import ffmpeg
from grillmaster.media.video import concat_list_text
from grillmaster.package.errors import PackageError
from grillmaster.package.render import (
    PACKAGE_ENCODE_ARGS,
    PACKAGE_ENCODE_CONCURRENCY,
    PACKAGE_FRAME_HEIGHT,
    PACKAGE_FRAME_WIDTH,
    PACKAGE_LEAD_TRIM_SECONDS,
    PACKAGE_OUTPUT_FPS,
    check_output_duration,
    check_subtitles_under,
    package_output_duration,
    render_progress,
    render_subtitled_range,
    run_in_pool,
    run_tracked,
)

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable, Sequence

    from grillmaster.events.bus import EventSink
    from grillmaster.media.ffmpeg import FfmpegRunner
    from grillmaster.package.pools import Cut, MediaPool
    from grillmaster.package.render import BurnPlan, RenderProgress

NOISE_CUT_SECONDS = 60
REMIX_TARGET_SEGMENT_SECONDS = 15 * 60
REMIX_MIN_SEGMENT_COUNT = 2
REMIX_MIN_SEGMENT_SECONDS = 60

_NOISE_VIDEO_FILTER = (
    f"scale={PACKAGE_FRAME_WIDTH}:{PACKAGE_FRAME_HEIGHT}:flags=bicubic,"
    "format=yuv420p,"
    f"fps={PACKAGE_OUTPUT_FPS}"
)
_NOISE_AUDIO_FILTER = "aformat=sample_rates=44100:channel_layouts=stereo"


@dataclass(frozen=True, slots=True)
class RemixPlan:
    """The content segments of a remix, picked before any pool draw."""

    segments: tuple[TimeRange, ...]


def plan_remix(
    runner: FfmpegRunner, *, video: Path, subtitles: Sequence[TimeRange]
) -> RemixPlan:
    """Split `video` after the lead trim at the `subtitles` cue gaps (the
    finalized SRT's ranges); raises `PackageError` when it cannot."""
    duration = probe.duration(runner, video)
    segments = select_remix_segments(
        subtitles, duration, start_seconds=PACKAGE_LEAD_TRIM_SECONDS
    )
    logger.info(f"Remix {video}: {len(segments)} segment(s) over {duration:.3f}s")
    for index, segment in enumerate(segments, start=1):
        logger.info(
            f"Remix segment {index}/{len(segments)}: "
            f"{segment.start:.3f}s-{segment.end:.3f}s"
        )
    return RemixPlan(tuple(segments))


def render_remix(
    runner: FfmpegRunner,
    *,
    video: Path,
    burn: BurnPlan,
    plan: RemixPlan,
    noise: MediaPool,
    target_dir: Path,
    events: EventSink,
) -> list[Path]:
    """Render the planned remix parts of `video` into `target_dir`; returns them.

    One noise cut per part is reserved from `noise` before any render, so a
    failed run still consumes it. Each part is probed against its expected
    length (see `check_output_duration`).
    """
    check_subtitles_under(video, burn)
    segments = plan.segments
    cuts = noise.reserve_seconds(NOISE_CUT_SECONDS, count=len(segments), ffmpeg=runner)
    outputs = [target_dir / f"{index}.mp4" for index in range(1, len(segments) + 1)]
    total = sum(
        package_output_duration(segment.duration) + cut.duration
        for segment, cut in zip(segments, cuts, strict=True)
    )
    with render_progress(
        events, "package:remix", "Remixing subtitles", total
    ) as progress:
        # Segments are independent files, and one encode never saturates
        # the card, so they render side by side against one shared bar.
        jobs: list[Callable[[threading.Event], None]] = [
            lambda event, segment=segment, cut=cut, output=output: build_remix_output(
                runner,
                video=video,
                burn=burn,
                output=output,
                head_noise=cut,
                segment=segment,
                progress=progress,
                abort=event,
            )
            for segment, cut, output in zip(segments, cuts, outputs, strict=True)
        ]
        run_in_pool(jobs, max_workers=PACKAGE_ENCODE_CONCURRENCY)
    return outputs


def build_remix_output(
    runner: FfmpegRunner,
    *,
    video: Path,
    burn: BurnPlan,
    output: Path,
    head_noise: Cut,
    segment: TimeRange,
    progress: RenderProgress,
    abort: threading.Event | None = None,
) -> None:
    """Create one noise + subtitled segment remix output and check its length."""
    temp_dir = Path(tempfile.mkdtemp(prefix="grill_remix_"))
    try:
        head = temp_dir / "head.mp4"
        target = temp_dir / "target.mp4"
        encode_noise_segment(
            runner,
            head_noise,
            head,
            progress=progress,
            note=f"Noise for {output.name}",
            abort=abort,
        )
        render_subtitled_range(
            runner,
            video=video,
            burn=burn,
            output=target,
            parts=[segment],
            progress=progress,
            note=f"Remixing {output.name}",
            abort=abort,
        )
        concat_remix_segments(runner, [head, target], output, abort=abort)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    check_output_duration(
        runner, output, head_noise.duration + package_output_duration(segment.duration)
    )


def encode_noise_segment(
    runner: FfmpegRunner,
    cut: Cut,
    output: Path,
    *,
    progress: RenderProgress,
    note: str | None = None,
    abort: threading.Event | None = None,
) -> None:
    """Transcode one noise slice into the package output format.

    Format-only fit (1920x1080 yuv420p @ 29.94, 44100 stereo, same encoder as
    the content segments) so remix concat can stream-copy the video. The look
    filters — rotate, grade, grain, tempo, rubberband, noise bed — are not
    applied.
    """
    if cut.duration <= 0:
        raise ValueError("noise cut duration must be positive")
    if not cut.source.exists():
        raise PackageError(f"noise source not found: {cut.source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    logger.info(
        f"Encoding noise from {cut.source.name} at {cut.start:.3f}s "
        f"for {cut.duration:.3f}s"
    )
    argv = ffmpeg(
        "-ss",
        f"{cut.start:.3f}",
        "-t",
        f"{cut.duration:.3f}",
        "-i",
        str(cut.source),
        "-vf",
        _NOISE_VIDEO_FILTER,
        "-af",
        _NOISE_AUDIO_FILTER,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        *PACKAGE_ENCODE_ARGS,
        str(output),
    )
    run_tracked(runner, argv, progress.track(cut.duration, note), abort=abort)


def concat_remix_segments(
    runner: FfmpegRunner,
    inputs: Sequence[Path],
    output: Path,
    *,
    abort: threading.Event | None = None,
) -> None:
    """Concatenate normalized remix segments into an upload-safe MP4."""
    if not inputs:
        raise ValueError("inputs must not be empty")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="grill_concat_") as scratch:
        concat_list = Path(scratch) / "segments.txt"
        concat_list.write_text(concat_list_text(inputs), encoding="utf-8")
        argv = ffmpeg(
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
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
        )
        run_tracked(runner, argv, None, abort=abort)


def select_remix_segments(
    subtitles: Sequence[TimeRange],
    duration_seconds: float,
    start_seconds: float = 0.0,
) -> list[TimeRange]:
    """Split `start_seconds`..end into equal parts without cutting subtitles.

    The part count follows the whole video length. Each cut targets an equal
    share of what is left after the previous snapped cut, so one snap's
    drift is spread over the remaining parts.
    """
    ranges = sorted(subtitles, key=lambda item: item.start)
    if not ranges:
        raise PackageError("no subtitle time ranges to split the remix on")
    if duration_seconds <= start_seconds:
        raise PackageError(f"video duration must exceed the {start_seconds}s start")

    segment_count = remix_segment_count(duration_seconds)
    splits: list[float] = []
    hi = duration_seconds - REMIX_MIN_SEGMENT_SECONDS
    for remaining_parts in range(segment_count, 1, -1):
        previous = splits[-1] if splits else start_seconds
        target = previous + (duration_seconds - previous) / remaining_parts
        lo = previous + REMIX_MIN_SEGMENT_SECONDS
        snapped = _snap_to_subtitle_break(ranges, duration_seconds, target, lo, hi)
        if snapped is not None:
            splits.append(snapped)

    boundaries = [start_seconds, *splits, duration_seconds]
    return [
        TimeRange(start, end)
        for start, end in itertools.pairwise(boundaries)
        if end > start
    ]


def remix_segment_count(duration_seconds: float) -> int:
    """Round the duration to whole target-length parts, at least the minimum.

    Rounds half up (not Python's half-to-even), so 37.5 minutes is 3 parts.
    """
    rounded = math.floor(duration_seconds / REMIX_TARGET_SEGMENT_SECONDS + 0.5)
    return max(REMIX_MIN_SEGMENT_COUNT, rounded)


def _snap_to_subtitle_break(
    ranges: Sequence[TimeRange],
    duration_seconds: float,
    target: float,
    lo: float,
    hi: float,
) -> float | None:
    if lo >= hi:
        return None

    candidates: list[float] = []
    for start, end in _positive_gaps(ranges, duration_seconds):
        gap_lo = max(start, lo)
        gap_hi = min(end, hi)
        if gap_hi <= gap_lo:
            continue
        if gap_lo <= target <= gap_hi:
            candidates.append(target)
        else:
            candidates.append((gap_lo + gap_hi) / 2)

    candidates.extend(
        boundary
        for time_range in ranges
        for boundary in (time_range.start, time_range.end)
        if lo < boundary < hi
    )

    usable = [candidate for candidate in candidates if lo < candidate < hi]
    if not usable:
        return None
    return min(usable, key=lambda candidate: abs(candidate - target))


def _positive_gaps(
    ranges: Sequence[TimeRange], duration_seconds: float
) -> list[tuple[float, float]]:
    gaps: list[tuple[float, float]] = []
    previous_end = 0.0
    for time_range in ranges:
        if time_range.start > previous_end:
            gaps.append((previous_end, time_range.start))
        previous_end = max(previous_end, time_range.end)
    if previous_end < duration_seconds:
        gaps.append((previous_end, duration_seconds))
    return [(start, end) for start, end in gaps if end > start]
