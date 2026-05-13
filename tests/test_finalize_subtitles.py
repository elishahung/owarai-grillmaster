import shutil
import unittest
from pathlib import Path

from services.finalize.subtitles import (
    ASS_HEADER,
    _block_to_dialogue,
    _clean_text,
    _srt_timecode_to_ass,
    convert_file,
)
from services.srt import SrtBlock


class AssConverterTextCleaningTests(unittest.TestCase):
    def test_preserves_mid_sentence_comma(self):
        # Netflix TC: keep mid-sentence ，
        self.assertEqual(_clean_text("你好，今天天氣不錯"), "你好，今天天氣不錯")

    def test_removes_trailing_full_stop(self):
        self.assertEqual(_clean_text("今天天氣不錯。"), "今天天氣不錯")

    def test_preserves_mid_sentence_enumeration_comma(self):
        self.assertEqual(_clean_text("蘋果、橘子、香蕉"), "蘋果、橘子、香蕉")

    def test_preserves_mid_sentence_semicolon(self):
        self.assertEqual(_clean_text("一；二；三"), "一；二；三")

    def test_combined_punctuation(self):
        # Mid-sentence ，/、 are kept; mid-line 。 becomes ，; trailing 。 is stripped.
        self.assertEqual(
            _clean_text("你好，今天天氣不錯。蘋果、橘子、香蕉。"),
            "你好，今天天氣不錯，蘋果、橘子、香蕉",
        )

    def test_converts_mid_line_full_stop_to_comma(self):
        # Bare 。 mid-subtitle reads awkwardly; replace with ， for visual flow.
        self.assertEqual(_clean_text("第一句。第二句。"), "第一句，第二句")

    def test_normalizes_halfwidth_ellipsis(self):
        self.assertEqual(_clean_text("今晚的嘉賓是..."), "今晚的嘉賓是…")
        # 4+ half-width dots also collapse into a single …
        self.assertEqual(_clean_text("等等....再說"), "等等…再說")

    def test_collapses_consecutive_fullwidth_ellipsis(self):
        # 2+ full-width … collapse into a single …
        self.assertEqual(_clean_text("好啊……"), "好啊…")
        self.assertEqual(_clean_text("等等………再說"), "等等…再說")

    def test_collapses_mixed_ellipsis_runs(self):
        # Mixed half-width and full-width sequences collapse into a single …
        self.assertEqual(_clean_text("混雜...…再說"), "混雜…再說")
        self.assertEqual(_clean_text("混雜…...再說"), "混雜…再說")

    def test_collapses_midline_ellipsis(self):
        # U+22EF (⋯) is normalized to U+2026 (…); runs collapse to a single …
        self.assertEqual(_clean_text("好啊⋯"), "好啊…")
        self.assertEqual(_clean_text("好啊⋯⋯"), "好啊…")

    def test_collapses_mixed_midline_and_fullwidth_ellipsis(self):
        # Mixed ⋯/…/... runs collapse into a single …
        self.assertEqual(_clean_text("混雜⋯…再說"), "混雜…再說")
        self.assertEqual(_clean_text("混雜⋯...再說"), "混雜…再說")

    def test_preserves_single_fullwidth_ellipsis(self):
        # A lone … is already minimal; do not touch it.
        self.assertEqual(_clean_text("好啊…"), "好啊…")

    def test_preserves_question_and_exclamation(self):
        self.assertEqual(_clean_text("真的嗎？太好了！"), "真的嗎？太好了！")

    def test_preserves_quotes_parens_ellipsis_colon(self):
        # Inside-quote `…` is preserved; trailing `……` collapses to a single `…`.
        self.assertEqual(
            _clean_text("「結論：很好吃……」"),
            "「結論：很好吃…」",
        )
        self.assertEqual(_clean_text("（旁白）"), "（旁白）")

    def test_strips_terminal_punct_inside_single_quotes(self):
        self.assertEqual(_clean_text("「沒問題。」"), "「沒問題」")
        self.assertEqual(_clean_text("「閃到腰，」"), "「閃到腰」")
        # Question mark / exclamation before 」 must be preserved.
        self.assertEqual(_clean_text("「怎麼啦？」"), "「怎麼啦？」")
        self.assertEqual(_clean_text("「太好了！」"), "「太好了！」")
        # Mid-quote ， / 、 / ； are preserved; only the tail is stripped.
        self.assertEqual(
            _clean_text("結果他說：「好，沒問題，站起來。」"),
            "結果他說：「好，沒問題，站起來」",
        )

    def test_strips_terminal_punct_inside_nested_quotes(self):
        # 『 』 (nested quote) follows the same rule.
        self.assertEqual(_clean_text("『嵌套。』"), "『嵌套』")
        self.assertEqual(_clean_text("『嵌套？』"), "『嵌套？』")

    def test_strips_terminal_punct_inside_quotes_across_lines(self):
        # Line 1's trailing ， is removed by _LINE_EDGE_PUNCT;
        # line 2's 。 before 」 is removed by _QUOTE_TAIL_PUNCT.
        self.assertEqual(
            _clean_text("「閃到腰，\n痛得要命。」"),
            "「閃到腰\n痛得要命」",
        )

    def test_strips_leading_and_trailing_terminal_punctuation(self):
        # Trailing ， at line end is removed.
        self.assertEqual(_clean_text("你好，"), "你好")
        # Leading ， at line start is removed.
        self.assertEqual(_clean_text("，你好"), "你好")

    def test_processes_multiline_text_per_line(self):
        self.assertEqual(
            _clean_text("第一行，\n第二行。"),
            "第一行\n第二行",
        )


class AssTimecodeTests(unittest.TestCase):
    def test_converts_srt_timecode_to_ass_pair(self):
        start, end = _srt_timecode_to_ass("00:00:01,500 --> 00:00:02,750")
        self.assertEqual(start, "0:00:01.50")
        self.assertEqual(end, "0:00:02.75")

    def test_strips_hour_leading_zero(self):
        start, end = _srt_timecode_to_ass("01:23:45,678 --> 12:00:00,000")
        self.assertEqual(start, "1:23:45.67")
        self.assertEqual(end, "12:00:00.00")

    def test_truncates_milliseconds_to_centiseconds(self):
        # 999 ms → 99 cs (Aegisub-style truncation, not rounding).
        start, _ = _srt_timecode_to_ass("00:00:00,999 --> 00:00:01,000")
        self.assertEqual(start, "0:00:00.99")

    def test_rejects_invalid_timecode(self):
        with self.assertRaises(ValueError):
            _srt_timecode_to_ass("garbage")


class AssDialogueTests(unittest.TestCase):
    def test_block_to_dialogue_emits_default_style_and_zero_margins(self):
        block = SrtBlock(
            index=1,
            timecode="00:00:01,000 --> 00:00:02,000",
            text="你好，世界。",
        )
        line = _block_to_dialogue(block)
        self.assertEqual(
            line,
            "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,你好，世界",
        )

    def test_block_to_dialogue_converts_newline_to_ass_soft_break(self):
        block = SrtBlock(
            index=1,
            timecode="00:00:01,000 --> 00:00:02,000",
            text="第一行，\n第二行。",
        )
        line = _block_to_dialogue(block)
        self.assertIn("第一行\\N第二行", line)

    def test_block_with_empty_text_still_emits_dialogue(self):
        block = SrtBlock(
            index=1,
            timecode="00:00:01,000 --> 00:00:02,000",
            text="",
        )
        line = _block_to_dialogue(block)
        self.assertEqual(
            line,
            "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,",
        )


class AssConvertFileTests(unittest.TestCase):
    def _make_temp_dir(self) -> Path:
        base = Path(__file__).resolve().parents[1] / "tmp_test_artifacts"
        base.mkdir(parents=True, exist_ok=True)
        path = base / "tmp_ass_converter"
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_convert_file_writes_styled_ass_with_cleaned_text(self):
        tmp = self._make_temp_dir()
        srt_path = tmp / "input.srt"
        ass_path = tmp / "output.ass"

        srt_path.write_text(
            "1\n"
            "00:00:01,000 --> 00:00:02,000\n"
            "你好，世界。\n"
            "\n"
            "2\n"
            "00:00:03,500 --> 00:00:05,250\n"
            "真的嗎？太好了！\n",
            encoding="utf-8",
        )

        convert_file(srt_path, ass_path)

        out = ass_path.read_text(encoding="utf-8")
        self.assertTrue(out.startswith(ASS_HEADER))
        self.assertIn("[Script Info]", out)
        self.assertIn("PlayResX: 1920", out)
        self.assertIn("Style: Default,源泉圓體月 M,64,", out)
        self.assertIn(
            "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,你好，世界",
            out,
        )
        self.assertIn(
            "Dialogue: 0,0:00:03.50,0:00:05.25,Default,,0,0,0,,真的嗎？太好了！",
            out,
        )

    def test_convert_file_creates_output_parent_directory(self):
        tmp = self._make_temp_dir()
        srt_path = tmp / "input.srt"
        ass_path = tmp / "nested" / "out.ass"

        srt_path.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n你好。\n",
            encoding="utf-8",
        )

        convert_file(srt_path, ass_path)
        self.assertTrue(ass_path.exists())

    def test_convert_file_writes_finalized_srt_when_path_given(self):
        tmp = self._make_temp_dir()
        srt_path = tmp / "input.srt"
        ass_path = tmp / "output.ass"
        finalized_path = tmp / "output.finalized.srt"

        srt_path.write_text(
            "1\n"
            "00:00:01,000 --> 00:00:02,000\n"
            "「沒問題。」\n"
            "\n"
            "2\n"
            "00:00:03,500 --> 00:00:05,250\n"
            "今晚的嘉賓是……\n"
            "\n"
            "3\n"
            "00:00:06,000 --> 00:00:08,000\n"
            "真的嗎？太好了！\n",
            encoding="utf-8",
        )

        convert_file(srt_path, ass_path, finalized_srt_path=finalized_path)

        self.assertTrue(ass_path.exists())
        self.assertTrue(finalized_path.exists())

        out = finalized_path.read_text(encoding="utf-8")
        # Standard SRT structure: index, timecode, text, blank-line separator,
        # trailing newline.
        self.assertEqual(
            out,
            "1\n"
            "00:00:01,000 --> 00:00:02,000\n"
            "「沒問題」\n"
            "\n"
            "2\n"
            "00:00:03,500 --> 00:00:05,250\n"
            "今晚的嘉賓是…\n"
            "\n"
            "3\n"
            "00:00:06,000 --> 00:00:08,000\n"
            "真的嗎？太好了！\n",
        )

    def test_convert_file_skips_finalized_srt_by_default(self):
        tmp = self._make_temp_dir()
        srt_path = tmp / "input.srt"
        ass_path = tmp / "output.ass"
        finalized_path = tmp / "output.finalized.srt"

        srt_path.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n你好。\n",
            encoding="utf-8",
        )

        convert_file(srt_path, ass_path)

        self.assertTrue(ass_path.exists())
        self.assertFalse(finalized_path.exists())

    def test_convert_file_creates_finalized_srt_parent_directory(self):
        tmp = self._make_temp_dir()
        srt_path = tmp / "input.srt"
        ass_path = tmp / "output.ass"
        finalized_path = tmp / "nested" / "deep" / "out.finalized.srt"

        srt_path.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\n你好。\n",
            encoding="utf-8",
        )

        convert_file(srt_path, ass_path, finalized_srt_path=finalized_path)
        self.assertTrue(finalized_path.exists())


if __name__ == "__main__":
    unittest.main()
