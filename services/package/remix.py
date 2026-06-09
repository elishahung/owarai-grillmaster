"""Remix package split selection and output assembly."""
from __future__ import annotations

from pathlib import Path

from loguru import logger

from project import FINALIZED_SRT_FILE_NAME
from services.media import MediaProcessor, TimeRange
from services.package.errors import RemixPackageError
from services.package.noise import select_noise_chunks, write_noise_state
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
    """Create the two remix package MP4 files."""
    finalized_srt = source_root / FINALIZED_SRT_FILE_NAME
    if not finalized_srt.exists():
        raise RemixPackageError(f"finalized SRT not found: {finalized_srt}")

    noise_dir = package_root / "noise" / noise_name
    selection = select_noise_chunks(noise_dir)
    duration_seconds = MediaProcessor.get_media_duration(video_file)
    split_seconds = select_remix_split(finalized_srt, duration_seconds)
    logger.info(
        f"Remix split for {video_file}: {split_seconds:.3f}s "
        f"of {duration_seconds:.3f}s"
    )

    progress_task = (
        progress.start_stage("Remixing subtitles", total=duration_seconds)
        if progress is not None
        else None
    )
    try:
        MediaProcessor.build_remix_output(
            video_file=video_file,
            subtitle_file=subtitle_file,
            output_file=target_dir / "video_1.mp4",
            noise_file=selection.chunk_paths[0],
            start_seconds=0.0,
            end_seconds=split_seconds,
            progress=progress,
            progress_task=progress_task,
        )
        MediaProcessor.build_remix_output(
            video_file=video_file,
            subtitle_file=subtitle_file,
            output_file=target_dir / "video_2.mp4",
            noise_file=selection.chunk_paths[1],
            start_seconds=split_seconds,
            end_seconds=duration_seconds,
            progress=progress,
            progress_task=progress_task,
        )
    except Exception:
        if progress is not None:
            progress.finish(progress_task, "failed")
        raise
    if progress is not None:
        progress.finish(progress_task)
    write_noise_state(noise_dir, selection.next_index)


def select_remix_split(srt_file: Path, duration_seconds: float) -> float:
    """Choose a split point near the middle without cutting subtitle text."""
    ranges = _parse_srt_ranges(srt_file)
    if not ranges:
        raise RemixPackageError(f"no subtitle time ranges found: {srt_file}")
    if duration_seconds <= 0:
        raise RemixPackageError("video duration must be positive")

    midpoint = duration_seconds / 2
    gaps: list[tuple[float, float]] = []
    previous_end = 0.0
    for time_range in ranges:
        if time_range.start_seconds > previous_end:
            gaps.append((previous_end, time_range.start_seconds))
        previous_end = max(previous_end, time_range.end_seconds)
    if previous_end < duration_seconds:
        gaps.append((previous_end, duration_seconds))

    positive_gaps = [(start, end) for start, end in gaps if end > start]
    if positive_gaps:
        start, end = min(
            positive_gaps,
            key=lambda gap: abs(((gap[0] + gap[1]) / 2) - midpoint),
        )
        return (start + end) / 2

    boundaries = [
        boundary
        for time_range in ranges
        for boundary in (time_range.start_seconds, time_range.end_seconds)
        if 0 < boundary < duration_seconds
    ]
    if not boundaries:
        raise RemixPackageError(
            f"no usable subtitle boundary found: {srt_file}"
        )
    return min(boundaries, key=lambda boundary: abs(boundary - midpoint))


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
