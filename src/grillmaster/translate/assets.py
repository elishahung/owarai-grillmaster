"""Reference frames and audio for the pre-pass and the chunk translators.

Frames are sampled at SRT block starts (+0.2 s, past the cut) spread evenly
over the blocks: the pre-pass takes 20-40 over the whole film, a chunk one
per `interval_s` of its time range (at most one per block); intervals are
positive by config validation. Stills and audio
slices are fixed-name caches: an existing file is reused as-is. A frame that
cannot be extracted is skipped with a warning; the pre-sampled frames are a
convenience, and the agent can still fetch any moment with `get_frames`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.media import probe
from grillmaster.media.audio import extract_audio_segment
from grillmaster.media.errors import MediaError
from grillmaster.media.frames import extract_frames

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from grillmaster.core.srt import SrtBlock
    from grillmaster.core.timecode import TimeRange
    from grillmaster.media.ffmpeg import FfmpegRunner
    from grillmaster.translate.chunker import Chunk

PREPASS_MIN_FRAMES = 20
PREPASS_MAX_FRAMES = 40
FRAME_START_OFFSET_S = 0.2
# A fast seek lands on the keyframe before the timestamp and then needs one
# decodable frame after it; staying clear of the trailing GOP (typically
# 0.5-1.0 s in TV muxes) keeps the last sample from landing on nothing.
VIDEO_END_MARGIN_S = 1.5


@dataclass(frozen=True, slots=True)
class Frame:
    time: float
    path: Path


@dataclass(frozen=True, slots=True)
class MediaAssets:
    """What one agent call sees: stills and, for audio backends, a track."""

    frames: tuple[Frame, ...]
    audio: Path | None


def prepass_frame_times(
    blocks: Sequence[SrtBlock], *, video_end: float, interval_s: int
) -> list[float]:
    """Pre-pass sample times: one per `interval_s` of SRT, clamped to 20-40
    and to the block count, each clamped to `[0, video_end]`."""
    if not blocks or video_end <= 0:
        return []
    srt_duration = max(0.0, blocks[-1].time_range.end)
    budget = int(srt_duration // interval_s)
    count = min(len(blocks), PREPASS_MAX_FRAMES, max(PREPASS_MIN_FRAMES, budget))
    return [
        round(
            min(max(block.time_range.start + FRAME_START_OFFSET_S, 0.0), video_end), 3
        )
        for block in _evenly_select(blocks, count)
    ]


def chunk_frame_times(chunk: Chunk, *, interval_s: int) -> list[float]:
    """Chunk sample times: one per `interval_s` of the chunk (at least one,
    at most one per block), each clamped into the chunk's time range."""
    span = chunk.time_range
    if span.duration <= 0:
        return []
    count = min(len(chunk.blocks), max(1, int(span.duration // interval_s)))
    return [
        round(
            min(
                max(block.time_range.start + FRAME_START_OFFSET_S, span.start), span.end
            ),
            3,
        )
        for block in _evenly_select(chunk.blocks, count)
    ]


def prepare_prepass_assets(
    runner: FfmpegRunner,
    *,
    video: Path,
    blocks: Sequence[SrtBlock],
    frames_dir: Path,
    interval_s: int,
    max_side: int,
    audio: Path | None,
) -> MediaAssets:
    """Whole-film stills; `audio` (the full track, or `None` for a backend
    that cannot hear) is passed through unchanged."""
    video_end = max(0.0, probe.duration(runner, video) - VIDEO_END_MARGIN_S)
    times = prepass_frame_times(blocks, video_end=video_end, interval_s=interval_s)
    frames = extract_available_frames(runner, video, times, frames_dir, max_side)
    return MediaAssets(frames=frames, audio=audio)


def prepare_chunk_assets(
    runner: FfmpegRunner,
    *,
    video: Path,
    chunk: Chunk,
    frames_dir: Path,
    interval_s: int,
    max_side: int,
    audio_source: Path | None,
    audio_out: Path,
) -> MediaAssets:
    """The chunk's stills and, when `audio_source` is given, its audio slice
    cut to the chunk's time range at `audio_out`."""
    times = chunk_frame_times(chunk, interval_s=interval_s)
    audio = None
    if audio_source is not None:
        slice_audio(runner, audio_source, audio_out, chunk.time_range)
        audio = audio_out
    frames = extract_available_frames(runner, video, times, frames_dir, max_side)
    return MediaAssets(frames=frames, audio=audio)


def slice_audio(runner: FfmpegRunner, source: Path, out: Path, span: TimeRange) -> None:
    """Cut `span` of `source` to `out`, unless `out` already exists."""
    if out.exists():
        return
    logger.debug(f"Extracting audio {span.start:.3f}-{span.end:.3f}s to {out}")
    extract_audio_segment(runner, source, out, span)


def extract_available_frames(
    runner: FfmpegRunner,
    video: Path,
    times: Sequence[float],
    out_dir: Path,
    max_side: int,
) -> tuple[Frame, ...]:
    """Stills at `times`, skipping (with a warning) the ones that fail."""
    if not times:
        return ()
    try:
        paths = extract_frames(runner, video, times, out_dir, max_side)
        return tuple(Frame(time, path) for time, path in zip(times, paths, strict=True))
    except MediaError:
        pass
    # Find the failing stills one by one; the good ones are cached by now.
    frames: list[Frame] = []
    for time in times:
        try:
            [path] = extract_frames(runner, video, [time], out_dir, max_side)
        except MediaError as error:
            logger.warning(f"Skipping frame at {time:.3f}s: {error}")
            continue
        frames.append(Frame(time, path))
    return tuple(frames)


def _evenly_select[T](items: Sequence[T], count: int) -> Sequence[T]:
    if count <= 0:
        return []
    if count >= len(items):
        return items
    if count == 1:
        return [items[0]]
    last = len(items) - 1
    return [items[round(slot * last / (count - 1))] for slot in range(count)]
