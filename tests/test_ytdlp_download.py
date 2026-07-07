import tempfile
import unittest
from pathlib import Path

from services.ytdlp.download import (
    _JpegThumbnailFixupPP,
    parse_section_time,
)

JPEG_MAGIC = b"\xff\xd8\xff\xe0" + b"\x00" * 16
PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class JpegThumbnailFixupTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _run(self, info):
        files_to_delete, info = _JpegThumbnailFixupPP().run(info)
        self.assertEqual(files_to_delete, [])
        return info

    def test_mislabeled_jpeg_png_is_renamed(self):
        # Abema slot thumbnails: JPEG bytes served under a .png filename.
        thumb = self.root / "0.png"
        thumb.write_bytes(JPEG_MAGIC)
        info = {
            "thumbnails": [{"filepath": str(thumb)}],
            "__files_to_move": {str(thumb): str(self.root / "poster.png")},
        }

        info = self._run(info)

        jpg = self.root / "0.jpg"
        self.assertFalse(thumb.exists())
        self.assertTrue(jpg.exists())
        self.assertEqual(info["thumbnails"][0]["filepath"], str(jpg))
        self.assertEqual(
            info["__files_to_move"],
            {str(jpg): str(self.root / "poster.jpg")},
        )

    def test_genuine_png_is_untouched(self):
        thumb = self.root / "0.png"
        thumb.write_bytes(PNG_MAGIC)
        info = {
            "thumbnails": [{"filepath": str(thumb)}],
            "__files_to_move": {str(thumb): str(self.root / "poster.png")},
        }

        info = self._run(info)

        self.assertTrue(thumb.exists())
        self.assertEqual(info["thumbnails"][0]["filepath"], str(thumb))
        self.assertEqual(
            info["__files_to_move"],
            {str(thumb): str(self.root / "poster.png")},
        )

    def test_correct_jpg_extension_is_untouched(self):
        thumb = self.root / "0.jpg"
        thumb.write_bytes(JPEG_MAGIC)
        info = {"thumbnails": [{"filepath": str(thumb)}]}

        info = self._run(info)

        self.assertTrue(thumb.exists())
        self.assertEqual(info["thumbnails"][0]["filepath"], str(thumb))

    def test_missing_or_absent_filepaths_are_tolerated(self):
        info = {
            "thumbnails": [
                {},
                {"filepath": str(self.root / "gone.png")},
            ]
        }

        self._run(info)


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
