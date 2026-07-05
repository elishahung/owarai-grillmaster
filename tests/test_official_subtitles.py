"""Official platform CC subtitle: normalization + prompt injection."""

import shutil
import unittest
import uuid
from pathlib import Path

from services.fixed_glossary import FixedGlossary
from services.media import TimeRange
from services.srt import SrtBlock, parse_srt
from services.translate.assets import ChunkMediaAssets
from services.translate.chunk.chunk_worker import (
    _build_user_message as build_chunk_user_message,
    _slice_official_subtitle,
)
from services.translate.pre_pass.pre_pass import (
    _build_user_message as build_pre_pass_user_message,
)
from services.translate.pre_pass.prompts import OFFICIAL_SUBTITLE_INSTRUCTION
from services.translate.pre_pass.schema import PrePassResult, SegmentSummary
from services.ytdlp import normalize_official_subtitle


def _block(index: int, start: str, end: str, text: str) -> SrtBlock:
    return SrtBlock(index=index, timecode=f"{start} --> {end}", text=text)


_RAW_SRT = (
    "1\n00:00:01,000 --> 00:00:03,000\n（田中）こんばんは\n\n"
    "2\n00:01:00,000 --> 00:01:02,500\nよろしくお願いします\n\n"
    "3\n00:02:30,000 --> 00:02:33,000\nありがとうございました\n"
)


class NormalizeOfficialSubtitleTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / "tmp_test_artifacts"
        base.mkdir(parents=True, exist_ok=True)
        self.root = base / f"tmp_official_{uuid.uuid4().hex[:8]}"
        self.root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.output_path = self.root / "video.official.ja.srt"

    def _write_raw(self, name: str = "0.ja.srt") -> Path:
        raw_path = self.root / name
        raw_path.write_text(_RAW_SRT, encoding="utf-8")
        return raw_path

    def test_no_raw_files_is_noop(self):
        self.assertFalse(normalize_official_subtitle([], self.output_path))
        self.assertFalse(self.output_path.exists())

    def test_single_raw_file_normalized_and_consumed(self):
        raw_path = self._write_raw()
        self.assertTrue(
            normalize_official_subtitle([raw_path], self.output_path)
        )
        self.assertFalse(raw_path.exists())
        blocks = parse_srt(self.output_path.read_text(encoding="utf-8"))
        self.assertEqual([b.index for b in blocks], [1, 2, 3])
        self.assertEqual(blocks[0].text, "（田中）こんばんは")
        self.assertEqual(blocks[0].timecode, "00:00:01,000 --> 00:00:03,000")

    def test_multiple_video_parts_skipped(self):
        raw_a = self._write_raw("0.ja.srt")
        raw_b = self._write_raw("1.ja.srt")
        self.assertFalse(
            normalize_official_subtitle([raw_a, raw_b], self.output_path)
        )
        self.assertFalse(self.output_path.exists())
        self.assertTrue(raw_a.exists())
        self.assertTrue(raw_b.exists())

    def test_language_variants_prefer_exact_ja(self):
        raw_variant = self._write_raw("0.ja-JP.srt")
        raw_exact = self._write_raw("0.ja.srt")
        self.assertTrue(
            normalize_official_subtitle(
                [raw_variant, raw_exact], self.output_path
            )
        )
        self.assertTrue(self.output_path.exists())
        # Both variants are consumed; the exact `ja` one was the source.
        self.assertFalse(raw_exact.exists())
        self.assertFalse(raw_variant.exists())

    def test_section_shift_filters_and_rebases(self):
        raw_path = self._write_raw()
        self.assertTrue(
            normalize_official_subtitle(
                [raw_path],
                self.output_path,
                section_start=60.0,
                section_end=140.0,
            )
        )
        blocks = parse_srt(self.output_path.read_text(encoding="utf-8"))
        # Block 1 ends before the section, block 3 starts after it.
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].index, 1)
        self.assertEqual(blocks[0].text, "よろしくお願いします")
        self.assertEqual(blocks[0].timecode, "00:00:00,000 --> 00:00:02,500")

    def test_section_with_no_overlap_writes_nothing(self):
        raw_path = self._write_raw()
        self.assertFalse(
            normalize_official_subtitle(
                [raw_path],
                self.output_path,
                section_start=300.0,
            )
        )
        self.assertFalse(self.output_path.exists())
        self.assertTrue(raw_path.exists())


class SliceOfficialSubtitleTests(unittest.TestCase):
    BLOCKS = [
        _block(1, "00:00:01,000", "00:00:03,000", "before"),
        _block(2, "00:01:00,000", "00:01:02,000", "inside"),
        _block(3, "00:02:00,000", "00:02:01,500", "padding-edge"),
        _block(4, "00:03:00,000", "00:03:05,000", "after"),
    ]

    def test_none_and_empty_input(self):
        self.assertEqual(_slice_official_subtitle(None, 0.0, 10.0), [])
        self.assertEqual(_slice_official_subtitle([], 0.0, 10.0), [])

    def test_overlap_selection_with_padding(self):
        # Chunk range 55s-120s; ±2s padding reaches the 120-122s block but
        # not the blocks ending at 3s or starting at 180s.
        selected = _slice_official_subtitle(self.BLOCKS, 55.0, 120.0)
        self.assertEqual([b.text for b in selected], ["inside", "padding-edge"])

    def test_block_just_outside_padding_excluded(self):
        selected = _slice_official_subtitle(self.BLOCKS, 5.001, 50.0)
        self.assertEqual(selected, [])


class ChunkUserMessageTests(unittest.TestCase):
    def _assets(self) -> ChunkMediaAssets:
        return ChunkMediaAssets(
            video_path=Path("video.mp4"),
            time_range=TimeRange(start_seconds=58.0, end_seconds=70.0),
            audio=None,
            frames=[],
            manifest_path=Path("chunk.json"),
            response_dir=Path("."),
        )

    def _pre_pass(self) -> PrePassResult:
        return PrePassResult(
            summary="summary",
            characters=[],
            proper_nouns={},
            glossary={},
            catchphrases=[],
            tone_notes="tone",
            segment_summaries=[
                SegmentSummary(from_index=1, to_index=1, summary="seg")
            ],
        )

    def _chunk(self) -> list[SrtBlock]:
        return [_block(1, "00:01:00,000", "00:01:02,000", "こんにちは")]

    def test_without_official_blocks_message_unchanged(self):
        message_default = build_chunk_user_message(
            self._chunk(), 0, 1, self._pre_pass(), self._assets()
        )
        message_none = build_chunk_user_message(
            self._chunk(),
            0,
            1,
            self._pre_pass(),
            self._assets(),
            official_subtitle_blocks=None,
        )
        self.assertEqual(message_default, message_none)
        self.assertNotIn("官方CC字幕參照", message_default)

    def test_with_official_blocks_injects_overlapping_slice(self):
        official = [
            _block(1, "00:00:01,000", "00:00:02,000", "範圍外"),
            _block(2, "00:01:00,500", "00:01:01,500", "（田中）正確台詞"),
        ]
        message = build_chunk_user_message(
            self._chunk(),
            0,
            1,
            self._pre_pass(),
            self._assets(),
            official_subtitle_blocks=official,
        )
        self.assertIn("官方CC字幕參照", message)
        self.assertIn("（田中）正確台詞", message)
        self.assertNotIn("範圍外", message)

    def test_no_overlap_omits_section(self):
        official = [_block(1, "00:10:00,000", "00:10:02,000", "遠處台詞")]
        message = build_chunk_user_message(
            self._chunk(),
            0,
            1,
            self._pre_pass(),
            self._assets(),
            official_subtitle_blocks=official,
        )
        self.assertNotIn("官方CC字幕參照", message)


class PrePassUserMessageTests(unittest.TestCase):
    def _build(self, official_subtitle_context: str | None) -> str:
        chunk = [_block(1, "00:00:01,000", "00:00:03,000", "こんにちは")]
        return build_pre_pass_user_message(
            "description",
            None,
            None,
            official_subtitle_context,
            FixedGlossary(),
            False,
            "SRT TEXT",
            [chunk],
            [],
        )

    def test_without_context_no_section(self):
        self.assertNotIn("官方CC字幕", self._build(None))

    def test_with_context_injects_full_text(self):
        message = self._build("1\n00:00:01,000 --> 00:00:03,000\nCC台詞\n")
        self.assertIn("官方CC字幕", message)
        self.assertIn("CC台詞", message)

    def test_instruction_block_loaded(self):
        self.assertIn("OFFICIAL CLOSED CAPTIONS", OFFICIAL_SUBTITLE_INSTRUCTION)


if __name__ == "__main__":
    unittest.main()
