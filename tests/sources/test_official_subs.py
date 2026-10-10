from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from grillmaster.core.srt import SrtBlock, read_srt_file
from grillmaster.sources.errors import SourceError
from grillmaster.sources.official_subs import (
    normalize_official_subtitles,
    raw_caption_files,
)

if TYPE_CHECKING:
    from pathlib import Path

RAW_SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\n（田中）こんばんは\n\n"
    "2\n00:01:00,000 --> 00:01:02,500\nよろしくお願いします\n\n"
    "3\n00:02:30,000 --> 00:02:33,000\nありがとうございました\n"
)


@pytest.fixture
def output(tmp_path: Path) -> Path:
    return tmp_path / "subs" / "ja.official.srt"


def write_raw(tmp_path: Path, name: str = "0.ja.srt", text: str = RAW_SRT) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_no_captions_writes_nothing(output: Path):
    assert normalize_official_subtitles([], output) is False
    assert not output.exists()


def test_single_caption_file_is_normalized_and_kept(tmp_path: Path, output: Path):
    raw = write_raw(tmp_path)

    assert normalize_official_subtitles([raw], output) is True

    blocks = read_srt_file(output)
    assert [block.index for block in blocks] == [1, 2, 3]
    assert blocks[0] == SrtBlock(
        1, "00:00:01,000 --> 00:00:03,000", "（田中）こんばんは"
    )
    assert raw.exists()


def test_captions_of_several_parts_write_nothing(tmp_path: Path, output: Path):
    raws = [write_raw(tmp_path, "0.ja.srt"), write_raw(tmp_path, "1.ja.srt")]

    assert normalize_official_subtitles(raws, output) is False
    assert not output.exists()


def test_language_variants_prefer_exact_ja(tmp_path: Path, output: Path):
    variant = write_raw(
        tmp_path, "0.ja-JP.srt", RAW_SRT.replace("こんばんは", "variant")
    )
    exact = write_raw(tmp_path, "0.ja.srt")

    assert normalize_official_subtitles([variant, exact], output) is True

    assert read_srt_file(output)[0].text == "（田中）こんばんは"


def test_section_filters_and_rebases(tmp_path: Path, output: Path):
    raw = write_raw(tmp_path)

    written = normalize_official_subtitles(
        [raw], output, section_start=60.0, section_end=140.0
    )

    # Block 1 ends before the section, block 3 starts after it.
    assert written is True
    assert read_srt_file(output) == [
        SrtBlock(1, "00:00:00,000 --> 00:00:02,500", "よろしくお願いします")
    ]


def test_section_without_overlap_writes_nothing(tmp_path: Path, output: Path):
    raw = write_raw(tmp_path)

    assert normalize_official_subtitles([raw], output, section_start=300.0) is False
    assert not output.exists()


@pytest.mark.parametrize(
    "text", ["", "1\nnot a timecode\ntext\n"], ids=["empty", "broken"]
)
def test_unusable_downloaded_captions_fail(tmp_path: Path, output: Path, text: str):
    raw = write_raw(tmp_path, text=text)

    with pytest.raises(SourceError):
        normalize_official_subtitles([raw], output)


def test_raw_caption_files_are_the_srt_files_beside_the_parts(tmp_path: Path):
    for name in ("0.mp4", "0.ja.srt", "0.ja-JP.srt"):
        (tmp_path / name).write_bytes(b"")

    assert [path.name for path in raw_caption_files(tmp_path)] == [
        "0.ja-JP.srt",
        "0.ja.srt",
    ]
