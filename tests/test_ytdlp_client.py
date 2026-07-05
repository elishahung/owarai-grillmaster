import unittest
import tempfile
from pathlib import Path

from services.ytdlp.client import (
    _BILIBILI_PLAYURL_FALLBACK_URL,
    _apply_bilibili_412_playurl_patch,
    _is_bilibili_input,
    get_ytdlp_client_for_url,
)


class BiliBiliPlayurlPatchTests(unittest.TestCase):
    def test_bilibili_input_detection(self):
        self.assertTrue(
            _is_bilibili_input("https://www.bilibili.com/video/BV16D4y1H7Wk")
        )
        self.assertTrue(_is_bilibili_input("BV16D4y1H7Wk"))
        self.assertFalse(_is_bilibili_input("https://tver.jp/episodes/ep123"))

    def test_bilibili_client_disables_default_cookiefile(self):
        with get_ytdlp_client_for_url(
            "https://www.bilibili.com/video/BV16D4y1H7Wk"
        ) as ydl:
            self.assertIsNone(ydl.params.get("cookiefile"))

    def test_explicit_cookiefile_option_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cookiefile = str(Path(temp_dir) / "explicit-cookies.txt")
            with get_ytdlp_client_for_url(
                "https://www.bilibili.com/video/BV16D4y1H7Wk",
                {"cookiefile": cookiefile},
            ) as ydl:
                self.assertEqual(ydl.params.get("cookiefile"), cookiefile)

    def test_patch_uses_non_wbi_playurl_endpoint(self):
        from yt_dlp.extractor.bilibili import BiliBiliIE

        _apply_bilibili_412_playurl_patch()

        calls = {}

        class FakeBiliBiliIE(BiliBiliIE):
            is_logged_in = False

            def _sign_wbi(self, params, video_id):
                calls["params"] = params
                calls["video_id"] = video_id
                return {"signed": "query"}

            def _download_json(self, url, video_id, *, query, headers, note):
                calls["url"] = url
                calls["download_video_id"] = video_id
                calls["query"] = query
                calls["headers"] = headers
                calls["note"] = note
                return {"data": {"ok": True}}

        result = FakeBiliBiliIE()._download_playinfo(
            "BV1ppJn62EWH",
            39087509391,
            headers={"Referer": "https://www.bilibili.com/"},
            query={"try_look": 1},
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls["url"], _BILIBILI_PLAYURL_FALLBACK_URL)
        self.assertEqual(calls["video_id"], "BV1ppJn62EWH")
        self.assertEqual(calls["download_video_id"], "BV1ppJn62EWH")
        self.assertEqual(calls["query"], {"signed": "query"})
        self.assertEqual(calls["params"]["cid"], 39087509391)
        self.assertEqual(calls["params"]["try_look"], 1)
        self.assertIn("Downloading video formats", calls["note"])


if __name__ == "__main__":
    unittest.main()
