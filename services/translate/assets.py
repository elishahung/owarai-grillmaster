"""Chunk/pre-pass media asset builders and persistent cache manifests."""

import json
from pathlib import Path

from loguru import logger
from pydantic import BaseModel

from services.media import MediaProcessor, TimeRange
from services.srt import SrtBlock

PRE_PASS_MIN_FRAMES = 20
PRE_PASS_MAX_FRAMES = 40
FRAME_START_OFFSET_SECONDS = 0.2


class FrameSpec(BaseModel):
    path: Path
    timestamp_seconds: float


class ChunkMediaAssets(BaseModel):
    video_path: Path
    time_range: TimeRange
    audio: Path | None
    frames: list[FrameSpec]
    manifest_path: Path
    response_dir: Path


class PrePassMediaAssets(BaseModel):
    audio: Path | None
    frames: list[FrameSpec]
    manifest_path: Path


def prepare_pre_pass_media_assets(
    video_path: Path,
    audio_path: Path,
    cache_root: Path,
    srt_blocks: list[SrtBlock],
    interval_seconds: int,
    max_side: int,
    extract_audio: bool = True,
) -> PrePassMediaAssets:
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    cache_root.mkdir(parents=True, exist_ok=True)
    frame_dir = cache_root / "media" / "frames"
    manifest_path = cache_root / "assets.json"

    duration = MediaProcessor.get_media_duration(video_path)
    # Fast-seek (-ss before -i in extract_video_frame) lands on the prior
    # keyframe; vframes=1 then needs at least one decodable frame ahead. Stay
    # clear of the trailing GOP (typically 0.5-1.0s in TV mux) so the seek
    # never lands on the last keyframe with no decodable frame after it.
    last_frame_offset = 1.5
    end_seconds = max(0.0, duration - last_frame_offset)
    timestamps = _pre_pass_srt_start_frame_timestamps(
        blocks=srt_blocks,
        video_end_seconds=end_seconds,
        interval_seconds=interval_seconds,
    )
    frames = [
        frame
        for frame in (
            _build_frame_asset(
                video_path=video_path,
                output_dir=frame_dir,
                timestamp_seconds=timestamp,
                max_side=max_side,
            )
            for timestamp in timestamps
        )
        if frame is not None
    ]
    audio = audio_path if extract_audio else None
    manifest_path.write_text(
        json.dumps(
            {
                "video_path": str(video_path),
                "audio": str(audio) if audio else None,
                "duration_seconds": duration,
                "interval_seconds": interval_seconds,
                "min_frames": PRE_PASS_MIN_FRAMES,
                "max_frames": PRE_PASS_MAX_FRAMES,
                "max_side": max_side,
                "frames": [
                    {
                        "timestamp_seconds": frame.timestamp_seconds,
                        "path": str(frame.path),
                    }
                    for frame in frames
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return PrePassMediaAssets(audio=audio, frames=frames, manifest_path=manifest_path)


def prepare_chunk_media_assets(
    video_path: Path,
    audio_path: Path,
    cache_root: Path,
    chunk: list[SrtBlock],
    chunk_index: int,
    total_chunks: int,
    interval_seconds: int,
    max_side: int,
    extract_audio: bool = True,
) -> ChunkMediaAssets:
    range_info = _chunk_time_range(chunk)
    chunk_slug = f"{chunk[0].index:04d}-{chunk[-1].index:04d}"

    manifests_dir = cache_root / "manifests"
    audio_dir = cache_root / "media" / "audio"
    frame_dir = cache_root / "media" / "frames"
    response_dir = cache_root / "responses"
    manifest_path = manifests_dir / f"chunk_{chunk_slug}.json"

    frame_timestamps = _chunk_srt_start_frame_timestamps(
        chunk=chunk,
        range_info=range_info,
        interval_seconds=interval_seconds,
    )

    audio: Path | None = None
    if extract_audio:
        audio = audio_dir / f"chunk_{chunk_slug}.ogg"
        MediaProcessor.extract_audio_segment(
            input_file=audio_path,
            output_file=audio,
            start_seconds=range_info.start_seconds,
            end_seconds=range_info.end_seconds,
        )
    frames = [
        frame
        for frame in (
            _build_frame_asset(
                video_path=video_path,
                output_dir=frame_dir,
                timestamp_seconds=timestamp,
                max_side=max_side,
            )
            for timestamp in frame_timestamps
        )
        if frame is not None
    ]

    manifests_dir.mkdir(parents=True, exist_ok=True)
    response_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "chunk_index": chunk_index,
                "total_chunks": total_chunks,
                "from_index": chunk[0].index,
                "to_index": chunk[-1].index,
                "time_range": range_info.model_dump(),
                "interval_seconds": interval_seconds,
                "max_side": max_side,
                "audio": str(audio) if audio else None,
                "frames": [frame.model_dump(mode="json") for frame in frames],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return ChunkMediaAssets(
        video_path=video_path,
        time_range=range_info,
        audio=audio,
        frames=frames,
        manifest_path=manifest_path,
        response_dir=response_dir,
    )


def _chunk_time_range(chunk: list[SrtBlock]) -> TimeRange:
    start = MediaProcessor.parse_timecode_line(chunk[0].timecode).start_seconds
    end = MediaProcessor.parse_timecode_line(chunk[-1].timecode).end_seconds
    return TimeRange(start_seconds=start, end_seconds=end)


def _chunk_srt_start_frame_timestamps(
    *,
    chunk: list[SrtBlock],
    range_info: TimeRange,
    interval_seconds: int,
) -> list[float]:
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if not chunk or range_info.duration_seconds <= 0:
        return []

    frame_budget = int(range_info.duration_seconds // interval_seconds)
    frame_count = min(len(chunk), max(1, frame_budget))
    selected_blocks = _evenly_select_blocks(chunk, frame_count)
    timestamps = []
    for block in selected_blocks:
        block_start = MediaProcessor.parse_timecode_line(block.timecode).start_seconds
        timestamp = min(
            max(
                block_start + FRAME_START_OFFSET_SECONDS,
                range_info.start_seconds,
            ),
            range_info.end_seconds,
        )
        timestamps.append(round(timestamp, 3))
    return timestamps


def _pre_pass_srt_start_frame_timestamps(
    *,
    blocks: list[SrtBlock],
    video_end_seconds: float,
    interval_seconds: int,
) -> list[float]:
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if not blocks or video_end_seconds <= 0:
        return []

    last_block_range = MediaProcessor.parse_timecode_line(blocks[-1].timecode)
    srt_duration = max(0.0, last_block_range.end_seconds)
    frame_budget = int(srt_duration // interval_seconds)
    frame_count = min(
        len(blocks),
        PRE_PASS_MAX_FRAMES,
        max(PRE_PASS_MIN_FRAMES, frame_budget),
    )
    selected_blocks = _evenly_select_blocks(blocks, frame_count)
    timestamps = []
    for block in selected_blocks:
        block_start = MediaProcessor.parse_timecode_line(block.timecode).start_seconds
        timestamp = min(
            max(block_start + FRAME_START_OFFSET_SECONDS, 0.0),
            video_end_seconds,
        )
        timestamps.append(round(timestamp, 3))
    return timestamps


def _evenly_select_blocks(blocks: list[SrtBlock], count: int) -> list[SrtBlock]:
    if count <= 0:
        return []
    if count >= len(blocks):
        return blocks
    if count == 1:
        return [blocks[0]]

    last_index = len(blocks) - 1
    return [blocks[round(slot * last_index / (count - 1))] for slot in range(count)]


def _build_frame_asset(
    video_path: Path,
    output_dir: Path,
    timestamp_seconds: float,
    max_side: int,
) -> FrameSpec | None:
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"frame_{timestamp_seconds:010.3f}_{max_side}.jpg"
    output_path = output_dir / filename
    try:
        MediaProcessor.extract_video_frame(
            input_file=video_path,
            output_file=output_path,
            timestamp_seconds=timestamp_seconds,
            max_side=max_side,
        )
    except Exception as e:
        logger.warning(f"Skipping frame at {timestamp_seconds:.3f}s: {e}")
        return None
    return FrameSpec(
        timestamp_seconds=timestamp_seconds,
        path=output_path,
    )
