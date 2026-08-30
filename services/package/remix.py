"""Remix package split selection and output assembly."""
from __future__ import annotations

from pathlib import Path

from loguru import logger

from project import FINALIZED_SRT_FILE_NAME
from services.media import (
    PACKAGE_LEAD_TRIM_SECONDS,
    MediaProcessor,
    TimeRange,
)
from services.package.constants import (
    NOISE_CUTS_PER_SEGMENT,
    REMIX_MIN_SEGMENT_SECONDS,
    REMIX_SEGMENT_SECONDS,
)
from services.package.errors import RemixPackageError
from services.package.noise import reserve_noise_cuts
from services.progress import NoopProgressReporter


def package_remix(
    source_root: Path,
    package_root: Path,
    target_dir: Path,
    video_file: Path,
    subtitle_file: Path,
    noise_name: str,
    progress: NoopProgressReporter | None = None,
) -> None:
    """Create remix package MP4 files."""
    finalized_srt = source_root / FINALIZED_SRT_FILE_NAME
    if not finalized_srt.exists():
        raise RemixPackageError(f"finalized SRT not found: {finalized_srt}")

    duration_seconds = MediaProcessor.get_media_duration(video_file)
    segments = select_remix_segments(finalized_srt, duration_seconds)
    logger.info(
        f"Remix {video_file}: {len(segments)} segment(s) "
        f"over {duration_seconds:.3f}s"
    )

    noise_dir = package_root / "noise" / noise_name
    noise_needed = NOISE_CUTS_PER_SEGMENT * len(segments)
    selection = reserve_noise_cuts(noise_dir, cut_count=noise_needed)
    noise_seconds = sum(cut.duration_seconds for cut in selection.cuts)

    progress_task = (
        progress.start_stage(
            "Remixing subtitles", total=duration_seconds + noise_seconds
        )
        if progress is not None
        else None
    )
    try:
        for index, segment in enumerate(segments):
            start_seconds = segment.start_seconds
            if index == 0:
                start_seconds += PACKAGE_LEAD_TRIM_SECONDS
            if start_seconds >= segment.end_seconds:
                raise RemixPackageError(
                    "first remix segment is shorter than the "
                    f"{PACKAGE_LEAD_TRIM_SECONDS}s package lead trim"
                )
            head = selection.cuts[NOISE_CUTS_PER_SEGMENT * index]
            tail = selection.cuts[NOISE_CUTS_PER_SEGMENT * index + 1]
            logger.info(
                f"Remix segment {index + 1}/{len(segments)}: "
                f"{start_seconds:.3f}s-{segment.end_seconds:.3f}s"
            )
            MediaProcessor.build_remix_output(
                video_file=video_file,
                subtitle_file=subtitle_file,
                output_file=target_dir / f"{index + 1}.mp4",
                head_noise=head,
                tail_noise=tail,
                start_seconds=start_seconds,
                end_seconds=segment.end_seconds,
                progress=progress,
                progress_task=progress_task,
            )
    except Exception:
        if progress is not None and progress_task is not None:
            progress.finish(progress_task, "failed")
        raise
    if progress is not None and progress_task is not None:
        progress.finish(progress_task)


def select_remix_segments(
    srt_file: Path, duration_seconds: float
) -> list[TimeRange]:
    """Split the video near every 8 minutes without cutting subtitle text."""
    ranges = _parse_srt_ranges(srt_file)
    if not ranges:
        raise RemixPackageError(f"no subtitle time ranges found: {srt_file}")
    if duration_seconds <= 0:
        raise RemixPackageError("video duration must be positive")

    splits: list[float] = []
    target = float(REMIX_SEGMENT_SECONDS)
    while target < duration_seconds - REMIX_MIN_SEGMENT_SECONDS:
        previous = splits[-1] if splits else 0.0
        lo = previous + REMIX_MIN_SEGMENT_SECONDS
        hi = duration_seconds - REMIX_MIN_SEGMENT_SECONDS
        snapped = _snap_to_subtitle_break(ranges, duration_seconds, target, lo, hi)
        if snapped is not None:
            splits.append(snapped)
        target += REMIX_SEGMENT_SECONDS

    boundaries = [0.0, *splits, duration_seconds]
    return [
        TimeRange(start_seconds=start, end_seconds=end)
        for start, end in zip(boundaries, boundaries[1:])
        if end > start
    ]


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
