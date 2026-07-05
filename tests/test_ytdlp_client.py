import unittest

from services.ytdlp.client import (
    _BILIBILI_PLAYURL_FALLBACK_URL,
    _apply_bilibili_412_playurl_patch,
)


class BiliBiliPlayurlPatchTests(unittest.TestCase):
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
