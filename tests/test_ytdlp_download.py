import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from yt_dlp.utils import DownloadError

from services.progress import NoopProgressReporter
from services.ytdlp.download import (
    _JpegThumbnailFixupPP,
    _ReporterProgressHook,
    download_video,
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


class _RecordingReporter(NoopProgressReporter):
    def __init__(self):
        self.events = []
        self._next = 1

    def start_stage(self, label, total=None):
        task_id = self._next
        self._next += 1
        self.events.append(("start_stage", label, total))
        return task_id

    def advance(self, task_id, amount=1.0, description=None):
        self.events.append(("advance", task_id, round(amount, 4)))

    def finish(self, task_id, status="done"):
        self.events.append(("finish", task_id, status))


class _ScreenOwningReporter(_RecordingReporter):
    owns_screen = True


def _captured_download_opts(progress) -> dict:
    """Run download_video with a mocked YoutubeDL, returning the opts used."""
    captured = {}

    def fake_ydl(opts):
        captured.update(opts)
        ydl = MagicMock()
        ydl.__enter__ = MagicMock(return_value=ydl)
        ydl.__exit__ = MagicMock(return_value=False)
        ydl.extract_info.return_value = {"title": "t"}
        return ydl

    with patch(
        "services.ytdlp.download.yt_dlp.YoutubeDL", side_effect=fake_ydl
    ):
        download_video(
            "https://example.com/v", Path("out"), progress=progress
        )
    return captured


class DownloadProgressModeTests(unittest.TestCase):
    def test_screen_owning_reporter_switches_to_hooks(self):
        opts = _captured_download_opts(_ScreenOwningReporter())
        self.assertTrue(opts.get("noprogress"))
        self.assertEqual(len(opts.get("progress_hooks", [])), 1)
        self.assertIsInstance(
            opts["progress_hooks"][0], _ReporterProgressHook
        )
        self.assertIn("logger", opts)

    def test_plain_reporter_keeps_native_renderer(self):
        opts = _captured_download_opts(_RecordingReporter())
        self.assertNotIn("noprogress", opts)
        self.assertNotIn("progress_hooks", opts)
        self.assertNotIn("logger", opts)

    def test_no_reporter_keeps_native_renderer(self):
        opts = _captured_download_opts(None)
        self.assertNotIn("progress_hooks", opts)


class DownloadFailureTests(unittest.TestCase):
    def test_failure_raises_without_a_second_attempt(self):
        def factory(opts):
            ydl = MagicMock()
            ydl.__enter__ = MagicMock(return_value=ydl)
            ydl.__exit__ = MagicMock(return_value=False)
            ydl.extract_info.side_effect = DownloadError("dead")
            return ydl

        with patch(
            "services.ytdlp.download.yt_dlp.YoutubeDL", side_effect=factory
        ) as ydl_cls:
            with self.assertRaises(DownloadError):
                download_video("https://example.com/v", Path("out"))

        self.assertEqual(ydl_cls.call_count, 1)


class AbemaAuthCacheResetTests(unittest.TestCase):
    """The download must re-authorize so it gets its own license handler."""

    def _run_download(self, url):
        def factory(opts):
            ydl = MagicMock()
            ydl.__enter__ = MagicMock(return_value=ydl)
            ydl.__exit__ = MagicMock(return_value=False)
            ydl.extract_info.return_value = {"title": "t"}
            return ydl

        with patch(
            "services.ytdlp.download.yt_dlp.YoutubeDL", side_effect=factory
        ):
            download_video(url, Path("out"))

    def _seed_token_cache(self):
        from yt_dlp.extractor.abematv import AbemaTVBaseIE

        for attr in ("_USERTOKEN", "_DEVICE_ID", "_MEDIATOKEN"):
            self.addCleanup(
                setattr, AbemaTVBaseIE, attr, getattr(AbemaTVBaseIE, attr)
            )
            setattr(AbemaTVBaseIE, attr, "stale")
        return AbemaTVBaseIE

    def test_abema_download_clears_extractor_token_cache(self):
        ie = self._seed_token_cache()
        self._run_download("https://abema.tv/video/episode/90-979_s1_p299")

        self.assertIsNone(ie._USERTOKEN)
        self.assertIsNone(ie._DEVICE_ID)
        self.assertIsNone(ie._MEDIATOKEN)

    def test_other_sources_leave_the_token_cache_alone(self):
        ie = self._seed_token_cache()
        self._run_download("https://example.com/v")

        self.assertEqual(ie._USERTOKEN, "stale")


class ReporterProgressHookTests(unittest.TestCase):
    def test_hook_emits_bar_per_file_with_throttle(self):
        reporter = _RecordingReporter()
        hook = _ReporterProgressHook(reporter)

        with patch(
            "services.ytdlp.download.monotonic",
            side_effect=[1.0, 1.1, 2.0, 3.0],
        ):
            hook(
                {
                    "status": "downloading",
                    "filename": "out/0.mp4",
                    "downloaded_bytes": 25,
                    "total_bytes": 100,
                }
            )
            # Second event inside the throttle window is dropped.
            hook(
                {
                    "status": "downloading",
                    "filename": "out/0.mp4",
                    "downloaded_bytes": 30,
                    "total_bytes": 100,
                }
            )
            hook(
                {
                    "status": "downloading",
                    "filename": "out/0.mp4",
                    "downloaded_bytes": 75,
                    "total_bytes": 100,
                }
            )
            hook({"status": "finished", "filename": "out/0.mp4"})

        self.assertEqual(
            reporter.events,
            [
                ("start_stage", "Downloading 0.mp4", 1.0),
                ("advance", 1, 0.25),
                ("advance", 1, 0.5),
                ("advance", 1, 0.25),  # top-up to 100% on finish
                ("finish", 1, "done"),
            ],
        )

    def test_new_filename_finishes_previous_bar(self):
        reporter = _RecordingReporter()
        hook = _ReporterProgressHook(reporter)
        with patch(
            "services.ytdlp.download.monotonic",
            side_effect=[1.0, 2.0],
        ):
            hook(
                {
                    "status": "downloading",
                    "filename": "out/0.mp4",
                    "downloaded_bytes": 50,
                    "total_bytes": 100,
                }
            )
            hook(
                {
                    "status": "downloading",
                    "filename": "out/1.mp4",
                    "downloaded_bytes": 0,
                    "total_bytes": 100,
                }
            )
        event_names = [event[0] for event in reporter.events]
        self.assertEqual(
            event_names,
            ["start_stage", "advance", "advance", "finish", "start_stage"],
        )


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
