"""Remix package split selection and output assembly."""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from loguru import logger

from grillmaster.project import FINALIZED_SRT_FILE_NAME
from grillmaster.services.media import (
    PACKAGE_ENCODE_CONCURRENCY,
    PACKAGE_LEAD_TRIM_SECONDS,
    BurnPlan,
    MediaProcessor,
    TimeRange,
    package_usable_duration,
)
from grillmaster.services.package.constants import (
    REMIX_MIN_SEGMENT_COUNT,
    REMIX_MIN_SEGMENT_SECONDS,
    REMIX_TARGET_SEGMENT_SECONDS,
)
from grillmaster.services.package.errors import RemixPackageError
from grillmaster.services.package.noise import reserve_noise_cuts
from grillmaster.services.package.placeholder import copy_placeholder
from grillmaster.services.progress import NoopProgressReporter


def package_remix(
    source_root: Path,
    package_root: Path,
    target_dir: Path,
    video_file: Path,
    burn: BurnPlan,
    noise_name: str,
    progress: NoopProgressReporter | None = None,
) -> None:
    """Create remix package MP4 files."""
    finalized_srt = source_root / FINALIZED_SRT_FILE_NAME
    if not finalized_srt.exists():
        raise RemixPackageError(f"finalized SRT not found: {finalized_srt}")

    duration_seconds = MediaProcessor.get_media_duration(video_file)
    content_seconds = package_usable_duration(duration_seconds)
    segments = select_remix_segments(
        finalized_srt,
        duration_seconds,
        start_seconds=PACKAGE_LEAD_TRIM_SECONDS,
    )
    logger.info(
        f"Remix {video_file}: {len(segments)} segment(s) over {duration_seconds:.3f}s"
    )

    copy_placeholder(package_root, target_dir)

    noise_dir = package_root / "noise" / noise_name
    selection = reserve_noise_cuts(noise_dir, cut_count=len(segments))
    noise_seconds = sum(cut.duration_seconds for cut in selection.cuts)

    renders: list[dict] = []
    for index, segment in enumerate(segments):
        logger.info(
            f"Remix segment {index + 1}/{len(segments)}: "
            f"{segment.start_seconds:.3f}s-{segment.end_seconds:.3f}s"
        )
        renders.append(
            dict(
                video_file=video_file,
                burn=burn,
                output_file=target_dir / f"{index + 1}.mp4",
                head_noise=selection.cuts[index],
                start_seconds=segment.start_seconds,
                end_seconds=segment.end_seconds,
            )
        )

    progress_task = (
        progress.start_stage(
            "Remixing subtitles", total=content_seconds + noise_seconds
        )
        if progress is not None
        else None
    )
    # Segments are independent files, and one encode never saturates the
    # card, so they render side by side against a shared progress task.
    try:
        with ThreadPoolExecutor(max_workers=PACKAGE_ENCODE_CONCURRENCY) as pool:
            futures = [
                pool.submit(
                    MediaProcessor.build_remix_output,
                    progress=progress,
                    progress_task=progress_task,
                    **render,
                )
                for render in renders
            ]
            for future in futures:
                future.result()
    except Exception:
        if progress is not None and progress_task is not None:
            progress.finish(progress_task, "failed")
        raise
    if progress is not None and progress_task is not None:
        progress.finish(progress_task)


def select_remix_segments(
    srt_file: Path, duration_seconds: float, start_seconds: float = 0.0
) -> list[TimeRange]:
    """Split `start_seconds`..end into equal parts without cutting subtitles.

    The part count follows the whole video length. Each cut targets an equal
    share of what is left after the previous snapped cut, so one snap's drift
    is spread over the remaining parts.
    """
    ranges = _parse_srt_ranges(srt_file)
    if not ranges:
        raise RemixPackageError(f"no subtitle time ranges found: {srt_file}")
    if duration_seconds <= start_seconds:
        raise RemixPackageError(
            f"video duration must exceed the {start_seconds}s start"
        )

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
        TimeRange(start_seconds=start, end_seconds=end)
        for start, end in zip(boundaries, boundaries[1:])
        if end > start
    ]


def remix_segment_count(duration_seconds: float) -> int:
    """Round the duration to whole target-length parts, at least the minimum.

    Rounds half up (not Python's half-to-even), so 37.5 minutes is 3 parts.
    """
    rounded = math.floor(duration_seconds / REMIX_TARGET_SEGMENT_SECONDS + 0.5)
    return max(REMIX_MIN_SEGMENT_COUNT, rounded)


def _snap_to_subtitle_break(
    ranges: list[TimeRange],
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

    for time_range in ranges:
        for boundary in (time_range.start_seconds, time_range.end_seconds):
            if lo < boundary < hi:
                candidates.append(boundary)

    usable = [candidate for candidate in candidates if lo < candidate < hi]
    if not usable:
        return None
    return min(usable, key=lambda candidate: abs(candidate - target))


def _positive_gaps(
    ranges: list[TimeRange], duration_seconds: float
) -> list[tuple[float, float]]:
    gaps: list[tuple[float, float]] = []
    previous_end = 0.0
    for time_range in ranges:
        if time_range.start_seconds > previous_end:
            gaps.append((previous_end, time_range.start_seconds))
        previous_end = max(previous_end, time_range.end_seconds)
    if previous_end < duration_seconds:
        gaps.append((previous_end, duration_seconds))
    return [(start, end) for start, end in gaps if end > start]


def _parse_srt_ranges(srt_file: Path) -> list[TimeRange]:
    ranges: list[TimeRange] = []
    for line in srt_file.read_text(encoding="utf-8").splitlines():
        if "-->" not in line:
            continue
        try:
            ranges.append(MediaProcessor.parse_timecode_line(line))
        except (ValueError, IndexError) as e:
            raise RemixPackageError(
                f"invalid SRT timecode in {srt_file}: {line}"
            ) from e
    return sorted(ranges, key=lambda item: item.start_seconds)
