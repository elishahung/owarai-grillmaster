from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.fakes import FakeFfmpeg

from grillmaster.core.srt import SrtBlock
from grillmaster.core.timecode import format_timecode_line
from grillmaster.translate.assets import (
    chunk_frame_times,
    prepare_chunk_assets,
    prepare_prepass_assets,
    prepass_frame_times,
)
from grillmaster.translate.chunker import Chunk

if TYPE_CHECKING:
    from pathlib import Path


def _blocks(count: int, step: float, *, cue: float, last_cue: float) -> list[SrtBlock]:
    """`count` blocks every `step` seconds lasting `cue` (the last `last_cue`)."""
    return [
        SrtBlock(
            i + 1,
            format_timecode_line(
                i * step, i * step + (last_cue if i == count - 1 else cue)
            ),
            f"line {i + 1}",
        )
        for i in range(count)
    ]


def _ffmpeg_inputs(fake: FakeFfmpeg) -> list[list[str]]:
    return [argv for argv in fake.calls if argv[0] == "ffmpeg"]


# --- pre-pass ---------------------------------------------------------------


def test_prepass_samples_even_srt_starts_up_to_forty(tmp_path: Path) -> None:
    fake = FakeFfmpeg(duration=4055.0)
    blocks = _blocks(45, 90, cue=5, last_cue=90)

    assets = prepare_prepass_assets(
        fake,
        video=tmp_path / "video.mp4",
        blocks=blocks,
        frames_dir=tmp_path / "frames",
        interval_s=60,
        max_side=768,
        audio=tmp_path / "audio.ogg",
    )

    assert [frame.time for frame in assets.frames] == [
        round(slot * 44 / 39) * 90 + 0.2 for slot in range(40)
    ]
    assert len(_ffmpeg_inputs(fake)) == 40
    assert all(frame.path.is_file() for frame in assets.frames)
    assert assets.frames[0].path.name == "frame_000000.200_768.jpg"
    # The full track passes through unchanged.
    assert assets.audio == tmp_path / "audio.ogg"


def test_prepass_uses_at_least_twenty_frames() -> None:
    times = prepass_frame_times(
        _blocks(25, 10, cue=1, last_cue=10), video_end=253.5, interval_s=60
    )
    assert len(times) == 20
    assert (times[0], times[-1]) == (0.2, 240.2)


def test_prepass_frames_cap_at_block_count() -> None:
    times = prepass_frame_times(
        _blocks(5, 10, cue=1, last_cue=1), video_end=43.5, interval_s=60
    )
    assert times == [0.2, 10.2, 20.2, 30.2, 40.2]


def test_prepass_frames_stay_clear_of_the_video_end(tmp_path: Path) -> None:
    fake = FakeFfmpeg(duration=190.1)
    assets = prepare_prepass_assets(
        fake,
        video=tmp_path / "video.mp4",
        blocks=_blocks(20, 10, cue=1, last_cue=10),
        frames_dir=tmp_path / "frames",
        interval_s=60,
        max_side=768,
        audio=None,
    )
    # 190.1 s minus the 1.5 s keyframe margin.
    assert assets.frames[-1].time == 188.6
    assert assets.audio is None


def test_prepass_interval_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        prepare_prepass_assets(
            FakeFfmpeg(duration=10.0),
            video=tmp_path / "video.mp4",
            blocks=_blocks(1, 1, cue=1, last_cue=1),
            frames_dir=tmp_path / "frames",
            interval_s=0,
            max_side=768,
            audio=None,
        )


# --- chunks -----------------------------------------------------------------


def test_chunk_samples_one_frame_per_interval() -> None:
    chunk = Chunk(tuple(_blocks(20, 15, cue=5, last_cue=15)))
    assert chunk_frame_times(chunk, interval_s=30) == [
        0.2,
        30.2,
        60.2,
        90.2,
        120.2,
        165.2,
        195.2,
        225.2,
        255.2,
        285.2,
    ]


def test_chunk_frames_cap_at_block_count() -> None:
    chunk = Chunk(
        (
            SrtBlock(1, "00:00:00,000 --> 00:00:01,000", "a"),
            SrtBlock(2, "00:02:00,000 --> 00:02:01,000", "b"),
            SrtBlock(3, "00:04:59,000 --> 00:05:00,000", "c"),
        )
    )
    assert chunk_frame_times(chunk, interval_s=30) == [0.2, 120.2, 299.2]


def test_chunk_frame_clamps_to_the_range_end() -> None:
    chunk = Chunk((SrtBlock(10, "00:00:09,950 --> 00:00:10,000", "a"),))
    assert chunk_frame_times(chunk, interval_s=30) == [10.0]


def test_chunk_interval_must_be_positive() -> None:
    chunk = Chunk((SrtBlock(1, "00:00:00,000 --> 00:00:01,000", "a"),))
    with pytest.raises(ValueError, match="positive"):
        chunk_frame_times(chunk, interval_s=0)


def test_chunk_audio_slice_starts_at_the_first_srt_start(tmp_path: Path) -> None:
    fake = FakeFfmpeg()
    chunk = Chunk(
        (
            SrtBlock(1, "00:00:00,500 --> 00:00:02,000", "a"),
            SrtBlock(2, "00:01:59,000 --> 00:02:00,000", "b"),
        )
    )
    out = tmp_path / "chunk" / "audio.ogg"

    assets = prepare_chunk_assets(
        fake,
        video=tmp_path / "video.mp4",
        chunk=chunk,
        frames_dir=tmp_path / "chunk" / "frames",
        interval_s=60,
        max_side=768,
        audio_source=tmp_path / "audio.ogg",
        audio_out=out,
    )

    assert [frame.time for frame in assets.frames] == [0.7]
    assert assets.audio == out
    assert out.is_file()
    [audio_call] = [argv for argv in fake.calls if "libopus" in argv]
    assert audio_call[audio_call.index("-ss") + 1] == "0.500"
    assert audio_call[audio_call.index("-t") + 1] == "119.500"


def test_existing_audio_slice_is_reused(tmp_path: Path) -> None:
    fake = FakeFfmpeg()
    out = tmp_path / "audio.ogg"
    out.write_bytes(b"cached")
    chunk = Chunk((SrtBlock(1, "00:00:00,000 --> 00:00:05,000", "a"),))

    prepare_chunk_assets(
        fake,
        video=tmp_path / "video.mp4",
        chunk=chunk,
        frames_dir=tmp_path / "frames",
        interval_s=30,
        max_side=768,
        audio_source=tmp_path / "full.ogg",
        audio_out=out,
    )

    assert not [argv for argv in fake.calls if "libopus" in argv]
    assert out.read_bytes() == b"cached"


def test_no_audio_source_means_no_slice(tmp_path: Path) -> None:
    fake = FakeFfmpeg()
    chunk = Chunk((SrtBlock(1, "00:00:00,000 --> 00:00:05,000", "a"),))

    assets = prepare_chunk_assets(
        fake,
        video=tmp_path / "video.mp4",
        chunk=chunk,
        frames_dir=tmp_path / "frames",
        interval_s=30,
        max_side=768,
        audio_source=None,
        audio_out=tmp_path / "audio.ogg",
    )

    assert assets.audio is None
    assert not (tmp_path / "audio.ogg").exists()


def test_failed_frames_are_skipped(tmp_path: Path) -> None:
    # The fake writes nothing, so every extraction comes back empty.
    fake = FakeFfmpeg(write_output=False)
    chunk = Chunk(tuple(_blocks(3, 40, cue=5, last_cue=5)))

    assets = prepare_chunk_assets(
        fake,
        video=tmp_path / "video.mp4",
        chunk=chunk,
        frames_dir=tmp_path / "frames",
        interval_s=30,
        max_side=768,
        audio_source=None,
        audio_out=tmp_path / "audio.ogg",
    )

    assert assets.frames == ()
