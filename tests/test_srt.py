import unittest

from grillmaster.legacy.services.srt import (
    TIMECODE_LINE_REGEX,
    SrtBlock,
    format_timecode,
    parse_srt,
    serialize_srt,
)


class SrtBlockTests(unittest.TestCase):
    def test_raw_serializes_index_timecode_text(self):
        block = SrtBlock(
            index=1,
            timecode="00:00:01,000 --> 00:00:02,000",
            text="hello",
        )
        self.assertEqual(
            block.raw,
            "1\n00:00:01,000 --> 00:00:02,000\nhello",
        )

    def test_char_count_matches_raw_length(self):
        block = SrtBlock(
            index=1,
            timecode="00:00:01,000 --> 00:00:02,000",
            text="hi",
        )
        self.assertEqual(block.char_count, len(block.raw))


class ParseSrtTests(unittest.TestCase):
    def test_parses_standard_blocks(self):
        text = (
            "1\n00:00:01,000 --> 00:00:02,000\nhello\n"
            "\n"
            "2\n00:00:03,000 --> 00:00:04,000\nworld\n"
        )
        blocks = parse_srt(text)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0].index, 1)
        self.assertEqual(blocks[0].text, "hello")
        self.assertEqual(blocks[1].index, 2)
        self.assertEqual(blocks[1].text, "world")

    def test_tolerates_crlf_endings(self):
        text = (
            "1\r\n00:00:01,000 --> 00:00:02,000\r\nhello\r\n"
            "\r\n"
            "2\r\n00:00:03,000 --> 00:00:04,000\r\nworld\r\n"
        )
        blocks = parse_srt(text)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0].text, "hello")

    def test_preserves_multiline_text(self):
        text = "1\n00:00:01,000 --> 00:00:02,000\nline one\nline two\n"
        blocks = parse_srt(text)
        self.assertEqual(blocks[0].text, "line one\nline two")

    def test_skips_blocks_with_fewer_than_two_lines(self):
        # Lone "noise" lines between proper blocks are skipped silently.
        text = "stray\n\n1\n00:00:01,000 --> 00:00:02,000\nhello\n"
        blocks = parse_srt(text)
        self.assertEqual(len(blocks), 1)

    def test_raises_on_invalid_index(self):
        text = "x\n00:00:01,000 --> 00:00:02,000\nhello\n"
        with self.assertRaises(ValueError):
            parse_srt(text)

    def test_raises_on_invalid_timecode(self):
        text = "1\nnot a timecode\nhello\n"
        with self.assertRaises(ValueError):
            parse_srt(text)


class SerializeSrtTests(unittest.TestCase):
    def test_serializes_blocks_with_blank_separator_and_trailing_newline(self):
        blocks = [
            SrtBlock(
                index=1,
                timecode="00:00:01,000 --> 00:00:02,000",
                text="hello",
            ),
            SrtBlock(
                index=2,
                timecode="00:00:03,000 --> 00:00:04,000",
                text="world",
            ),
        ]
        self.assertEqual(
            serialize_srt(blocks),
            "1\n00:00:01,000 --> 00:00:02,000\nhello"
            "\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\nworld\n",
        )

    def test_round_trip_with_parse_srt(self):
        text = (
            "1\n00:00:01,000 --> 00:00:02,000\nhello\n"
            "\n"
            "2\n00:00:03,000 --> 00:00:04,000\nworld\n"
        )
        self.assertEqual(serialize_srt(parse_srt(text)), text)


class FormatTimecodeTests(unittest.TestCase):
    def test_zero_is_canonical(self):
        self.assertEqual(format_timecode(0.0), "00:00:00,000")

    def test_basic_seconds_and_milliseconds(self):
        self.assertEqual(format_timecode(1.5), "00:00:01,500")
        self.assertEqual(format_timecode(61.250), "00:01:01,250")

    def test_hour_boundary(self):
        self.assertEqual(format_timecode(3600.0), "01:00:00,000")

    def test_negative_clamps_to_zero(self):
        self.assertEqual(format_timecode(-5.0), "00:00:00,000")

    def test_rounds_milliseconds(self):
        # 0.9999s rounds to 1000 ms → 1.000
        self.assertEqual(format_timecode(0.9999), "00:00:01,000")


class TimecodeLineRegexTests(unittest.TestCase):
    def test_matches_canonical_form(self):
        self.assertIsNotNone(TIMECODE_LINE_REGEX.match("00:00:01,000 --> 00:00:02,000"))

    def test_matches_dot_milliseconds_separator(self):
        # Some producers use "." instead of "," before ms.
        self.assertIsNotNone(TIMECODE_LINE_REGEX.match("00:00:01.000 --> 00:00:02.000"))

    def test_rejects_unpadded_fields(self):
        self.assertIsNone(TIMECODE_LINE_REGEX.match("0:00:01,000 --> 0:00:02,000"))


if __name__ == "__main__":
    unittest.main()
