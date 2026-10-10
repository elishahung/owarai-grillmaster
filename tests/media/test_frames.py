from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from tests.fakes import FAKE_JPEG

from grillmaster.media.errors import MediaError
from grillmaster.media.ffmpeg import SubprocessFfmpegRunner, ffprobe
from grillmaster.media.frames import extract_frames, frame_path

if TYPE_CHECKING:
    from tests.fakes import FakeFfmpeg


def test_frame_path_uses_the_legacy_naming(tmp_path: Path):
    assert frame_path(tmp_path, 1234.5671, 768).name == "frame_001234.567_768.jpg"
    assert frame_path(tmp_path, 3.0, 512).name == "frame_000003.000_512.jpg"


def test_extracts_each_frame_in_input_order(fake_ffmpeg: FakeFfmpeg, tmp_path: Path):
    out_dir = tmp_path / "frames"
    paths = extract_frames(fake_ffmpeg, Path("video.mp4"), [9.0, 1.5], out_dir, 768)
    assert [p.name for p in paths] == [
        "frame_000009.000_768.jpg",
        "frame_000001.500_768.jpg",
    ]
    assert all(p.read_bytes() == FAKE_JPEG for p in paths)
    assert list(out_dir.glob(".*")) == []  # temp files were renamed away
    # Extraction is concurrent, so the calls arrive in any order.
    calls = {argv[argv.index("-ss") + 1]: argv for argv in fake_ffmpeg.calls}
    assert sorted(calls) == ["1.500", "9.000"]
    first = calls["9.000"]
    assert first[first.index("-i") + 1] == "video.mp4"
    assert first[first.index("-frames:v") + 1] == "1"
    assert first[first.index("-vf") + 1] == (
        "scale='if(gte(iw,ih),768,-2)':'if(gte(iw,ih),-2,768)'"
    )


def test_cached_and_repeated_frames_run_ffmpeg_once_each(
    fake_ffmpeg: FakeFfmpeg, tmp_path: Path
):
    frame_path(tmp_path, 2.0, 768).write_bytes(b"old")
    times = [1.0, 2.0, 3.0, 1.0, 4.0, 5.0, 6.0]
    paths = extract_frames(fake_ffmpeg, Path("video.mp4"), times, tmp_path, 768)
    assert paths == [frame_path(tmp_path, t, 768) for t in times]
    started = sorted(argv[argv.index("-ss") + 1] for argv in fake_ffmpeg.calls)
    assert started == ["1.000", "3.000", "4.000", "5.000", "6.000"]


def test_existing_frame_is_a_cache_hit(fake_ffmpeg: FakeFfmpeg, tmp_path: Path):
    cached = frame_path(tmp_path, 2.0, 768)
    cached.write_bytes(b"old")
    (path,) = extract_frames(fake_ffmpeg, Path("video.mp4"), [2.0], tmp_path, 768)
    assert path == cached
    assert path.read_bytes() == b"old"
    assert fake_ffmpeg.calls == []


def test_frame_size_is_part_of_the_cache_key(fake_ffmpeg: FakeFfmpeg, tmp_path: Path):
    frame_path(tmp_path, 2.0, 768).write_bytes(b"old")
    extract_frames(fake_ffmpeg, Path("video.mp4"), [2.0], tmp_path, 512)
    assert len(fake_ffmpeg.calls) == 1


def test_missing_output_raises_and_leaves_no_file(
    fake_ffmpeg: FakeFfmpeg, tmp_path: Path
):
    fake_ffmpeg.write_output = False
    with pytest.raises(MediaError, match=r"No frame extracted at 5\.000s"):
        extract_frames(fake_ffmpeg, Path("video.mp4"), [5.0], tmp_path, 768)
    assert list(tmp_path.iterdir()) == []


def test_rejects_non_positive_max_side(fake_ffmpeg: FakeFfmpeg, tmp_path: Path):
    with pytest.raises(ValueError, match="max_side"):
        extract_frames(fake_ffmpeg, Path("video.mp4"), [1.0], tmp_path, 0)


def test_real_extraction_scales_the_longest_side(media_fixture: Path, tmp_path: Path):
    runner = SubprocessFfmpegRunner()
    (path,) = extract_frames(runner, media_fixture, [1.0], tmp_path, 160)
    assert path.read_bytes()[:2] == b"\xff\xd8"  # JPEG SOI marker
    size = runner.run(
        ffprobe(
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(path),
        )
    )
    assert size.strip() == "160,90"


def test_real_extraction_past_the_end_raises(media_fixture: Path, tmp_path: Path):
    with pytest.raises(MediaError):
        extract_frames(SubprocessFfmpegRunner(), media_fixture, [30.0], tmp_path, 160)
