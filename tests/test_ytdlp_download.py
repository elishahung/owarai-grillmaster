import unittest

from services.ytdlp.download import parse_section_time


class ParseSectionTimeTests(unittest.TestCase):
    def test_plain_seconds(self):
        self.assertEqual(parse_section_time("90"), 90.0)

    def test_mm_ss(self):
        self.assertEqual(parse_section_time("1:30"), 90.0)

    def test_hh_mm_ss(self):
        self.assertEqual(parse_section_time("0:01:30"), 90.0)

    def test_invalid_value_raises(self):
        with self.assertRaises(ValueError):
            parse_section_time("abc")


if __name__ == "__main__":
    unittest.main()
