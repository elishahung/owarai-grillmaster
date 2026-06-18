import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from services.chunk_fix import (
    canonicalize_by_position,
    validate_chunk_structure,
)
from services.srt import parse_srt

_REPO_ROOT = Path(__file__).resolve().parents[1]
_VALIDATOR = _REPO_ROOT / "services" / "chunk_fix" / "validate_chunk.py"

# Three source blocks with distinct timecodes. Validation matches by timecode
# only (text is never compared), so candidate text can be anything.
_SOURCE_SRT = """1
00:00:01,000 --> 00:00:02,000
こんにちは

2
00:00:02,000 --> 00:00:03,000
さようなら

3
00:00:03,000 --> 00:00:04,000
おやすみ
"""

_GOOD_CANDIDATE = """1
00:00:01,000 --> 00:00:02,000
你好

2
00:00:02,000 --> 00:00:03,000
再見

3
00:00:03,000 --> 00:00:04,000
晚安
"""


def _source_blocks():
    return parse_srt(_SOURCE_SRT)


class ValidateChunkStructureTests(unittest.TestCase):
    def test_exact_match_returns_normalized_blocks(self):
        blocks = validate_chunk_structure(
            _source_blocks(), _GOOD_CANDIDATE, tolerance=0
        )
        self.assertEqual([b.index for b in blocks], [1, 2, 3])
        self.assertEqual(blocks[0].timecode, "00:00:01,000 --> 00:00:02,000")
        self.assertEqual(blocks[1].text, "再見")

    def test_missing_block_within_tolerance_is_empty(self):
        candidate = """1
00:00:01,000 --> 00:00:02,000
你好

3
00:00:03,000 --> 00:00:04,000
晚安
"""
        blocks = validate_chunk_structure(
            _source_blocks(), candidate, tolerance=1
        )
        self.assertEqual(len(blocks), 3)
        self.assertEqual(blocks[1].text, "")  # missing block -> empty

    def test_missing_block_exceeding_tolerance_raises(self):
        candidate = """1
00:00:01,000 --> 00:00:02,000
你好
"""
        with self.assertRaises(ValueError) as ctx:
            validate_chunk_structure(_source_blocks(), candidate, tolerance=0)
        self.assertIn("exceeds tolerance", str(ctx.exception))

    def test_unexpected_timecode_raises(self):
        candidate = """1
00:00:09,000 --> 00:00:10,000
你好
"""
        with self.assertRaises(ValueError) as ctx:
            validate_chunk_structure(_source_blocks(), candidate, tolerance=2)
        self.assertIn("Unexpected output timecode", str(ctx.exception))

    def test_duplicate_timecode_raises(self):
        candidate = """1
00:00:01,000 --> 00:00:02,000
你好

2
00:00:01,000 --> 00:00:02,000
再見

3
00:00:03,000 --> 00:00:04,000
晚安
"""
        with self.assertRaises(ValueError) as ctx:
            validate_chunk_structure(_source_blocks(), candidate, tolerance=2)
        self.assertIn("Duplicate output timecodes", str(ctx.exception))


class CanonicalizeByPositionTests(unittest.TestCase):
    def test_matching_counts_reindexes_to_source_skeleton(self):
        # Wrong indices and wrong timecodes, but the right number of blocks.
        drifted = """5
99:99:99,999 --> 99:99:99,999
你好

6
88:88:88,888 --> 88:88:88,888
再見

7
77:77:77,777 --> 77:77:77,777
晚安
"""
        fixed = canonicalize_by_position(_SOURCE_SRT, drifted)
        self.assertIsNotNone(fixed)
        # Result adopts source skeleton; revalidates clean.
        blocks = validate_chunk_structure(_source_blocks(), fixed, tolerance=0)
        self.assertEqual([b.text for b in blocks], ["你好", "再見", "晚安"])

    def test_mismatched_counts_returns_none(self):
        two_blocks = """1
00:00:01,000 --> 00:00:02,000
你好

2
00:00:02,000 --> 00:00:03,000
再見
"""
        self.assertIsNone(canonicalize_by_position(_SOURCE_SRT, two_blocks))


class ValidateChunkCliTests(unittest.TestCase):
    def _run(self, source_text: str, candidate_text: str, tolerance: int):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "source.srt"
            cand = Path(tmp) / "candidate.srt"
            src.write_text(source_text, encoding="utf-8")
            cand.write_text(candidate_text, encoding="utf-8")
            return subprocess.run(
                [
                    sys.executable,
                    str(_VALIDATOR),
                    str(src),
                    str(cand),
                    "--tolerance",
                    str(tolerance),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )

    def test_cli_valid_exits_zero(self):
        result = self._run(_SOURCE_SRT, _GOOD_CANDIDATE, 0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VALID", result.stdout)

    def test_cli_invalid_exits_one_with_error(self):
        bad = """1
00:00:09,000 --> 00:00:10,000
你好
"""
        result = self._run(_SOURCE_SRT, bad, 0)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unexpected output timecode", result.stdout)


if __name__ == "__main__":
    unittest.main()
