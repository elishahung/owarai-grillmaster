import unittest

from services.srt import SrtBlock
from services.translate.chunk.normalizer import normalize_translated_blocks


class GeminiNormalizerTests(unittest.TestCase):
    def test_removes_trailing_empty_speaker_dash_line(self):
        blocks = [
            SrtBlock(
                index=396,
                timecode="00:17:11,680 --> 00:17:13,200",
                text="- 給我適可而止。\n-",
            )
        ]

        normalized = normalize_translated_blocks(blocks)

        self.assertEqual(normalized[0].text, "- 給我適可而止。")

    def test_removes_all_empty_speaker_dash_lines(self):
        blocks = [
            SrtBlock(
                index=416,
                timecode="00:18:17,320 --> 00:18:17,860",
                text="- \n-",
            )
        ]

        normalized = normalize_translated_blocks(blocks)

        self.assertEqual(normalized[0].text, "")
        self.assertEqual(normalized[0].index, 416)
        self.assertEqual(
            normalized[0].timecode, "00:18:17,320 --> 00:18:17,860"
        )

    def test_keeps_dash_lines_with_translated_content(self):
        blocks = [
            SrtBlock(
                index=1,
                timecode="00:00:00,000 --> 00:00:01,000",
                text="- 第一人。\n- 第二人。",
            )
        ]

        normalized = normalize_translated_blocks(blocks)

        self.assertEqual(normalized[0].text, "- 第一人。\n- 第二人。")


if __name__ == "__main__":
    unittest.main()
