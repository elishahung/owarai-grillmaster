"""Chunk/pre-pass media asset builders and persistent cache manifests."""

import hashlib
import json
from pathlib import Path

from google import genai
from loguru import logger
from pydantic import BaseModel

from services.media import MediaProcessor, TimeRange
from services.srt import SrtBlock


class LocalMediaRef(BaseModel):
    path: Path
    mime_type: str


class FrameSpec(LocalMediaRef):
    timestamp_seconds: float
    mime_type: str = "image/jpeg"


class ChunkMediaAssets(BaseModel):
    time_range: TimeRange
    audio: LocalMediaRef
    frames: list[FrameSpec]
    manifest_path: Path
    response_dir: Path


class PrePassMediaAssets(BaseModel):
    audio: LocalMediaRef
    frames: list[FrameSpec]
    manifest_path: Path


def media_ref_to_part(ref: LocalMediaRef) -> genai.types.Part:
    if not ref.path.exists():
        raise FileNotFoundError(f"Gemini media file not found: {ref.path}")
    return genai.types.Part.from_bytes(
        data=ref.path.read_bytes(),
        mime_type=ref.mime_type,
    )


def media_refs_to_parts(refs: list[LocalMediaRef]) -> list[genai.types.Part]:
    return [media_ref_to_part(ref) for ref in refs]


def prepare_pre_pass_media_assets(
    video_path: Path,
    audio_path: Path,
    cache_root: Path,
    interval_seconds: int,
    max_side: int,
    intro_skip_seconds: float,
) -> PrePassMediaAssets:
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
    # Skip the very first seconds (TV station intro/logo). Clamp so the start
    # never exceeds end on pathologically short videos.
    start_seconds = min(max(0.0, intro_skip_seconds), end_seconds)
    timestamps = MediaProcessor.absolute_interval_timestamps(
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        interval_seconds=interval_seconds,
        include_start=True,
        include_end=True,
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
    audio_ref = LocalMediaRef(path=audio_path, mime_type="audio/ogg")
    manifest_path.write_text(
        json.dumps(
            {
                "video_path": str(video_path),
                "audio": audio_ref.model_dump(mode="json"),
                "duration_seconds": duration,
                "interval_seconds": interval_seconds,
                "intro_skip_seconds": intro_skip_seconds,
                "max_side": max_side,
                "frames": [
                    {
                        "timestamp_seconds": frame.timestamp_seconds,
                        "path": str(frame.path),
                        "mime_type": frame.mime_type,
                    }
                    for frame in frames
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return PrePassMediaAssets(
        audio=audio_ref, frames=frames, manifest_path=manifest_path
    )


def prepare_chunk_media_assets(
    video_path: Path,
    audio_path: Path,
    cache_root: Path,
    video_key: str,
    chunk: list[SrtBlock],
    chunk_index: int,
    total_chunks: int,
    interval_seconds: int,
    max_side: int,
    intro_skip_seconds: float,
) -> ChunkMediaAssets:
    range_info = _chunk_time_range(chunk)
    chunk_slug = f"{chunk[0].index:04d}-{chunk[-1].index:04d}"

    manifests_dir = cache_root / "manifests"
    audio_dir = cache_root / "media" / "audio"
    frame_dir = cache_root / "media" / "frames"
    response_dir = cache_root / "responses"
    manifest_path = manifests_dir / f"chunk_{chunk_slug}.json"

    # Push the first chunk's first frame past the TV station intro/logo.
    # Audio segment + chunk time_range stay anchored to the subtitle range.
    frame_start = range_info.start_seconds
    if chunk_index == 0:
        frame_start = min(
            max(frame_start, intro_skip_seconds), range_info.end_seconds
        )
    frame_timestamps = MediaProcessor.absolute_interval_timestamps(
        start_seconds=frame_start,
        end_seconds=range_info.end_seconds,
        interval_seconds=interval_seconds,
        include_start=True,
        include_end=True,
    )

    digest = hashlib.sha256(
        (
            f"{video_key}:{chunk_slug}:{range_info.start_seconds:.3f}:"
            f"{range_info.end_seconds:.3f}:{interval_seconds}:{max_side}"
        ).encode("utf-8")
    ).hexdigest()[:10]
    audio_output = audio_dir / f"chunk_{chunk_slug}_{digest}.ogg"
    MediaProcessor.extract_audio_segment(
        input_file=audio_path,
        output_file=audio_output,
        start_seconds=range_info.start_seconds,
        end_seconds=range_info.end_seconds,
    )

    audio_ref = LocalMediaRef(path=audio_output, mime_type="audio/ogg")
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
                "intro_skip_seconds": intro_skip_seconds
                if chunk_index == 0
                else None,
                "max_side": max_side,
                "audio": audio_ref.model_dump(mode="json"),
                "frames": [frame.model_dump(mode="json") for frame in frames],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return ChunkMediaAssets(
        time_range=range_info,
        audio=audio_ref,
        frames=frames,
        manifest_path=manifest_path,
        response_dir=response_dir,
    )


def _chunk_time_range(chunk: list[SrtBlock]) -> TimeRange:
    start = MediaProcessor.parse_timecode_line(chunk[0].timecode).start_seconds
    end = MediaProcessor.parse_timecode_line(chunk[-1].timecode).end_seconds
    return TimeRange(start_seconds=start, end_seconds=end)


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
        logger.warning(
            f"Skipping frame at {timestamp_seconds:.3f}s: {e}"
        )
        return None
    return FrameSpec(
        timestamp_seconds=timestamp_seconds,
        path=output_path,
    )
