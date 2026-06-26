import json
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from services.translate.assets import (
    LocalMediaRef,
    media_ref_to_part,
    prepare_chunk_media_assets,
    prepare_pre_pass_media_assets,
)
from services.srt import SrtBlock, format_timecode


class GeminiAssetsTests(unittest.TestCase):
    def _make_temp_dir(self) -> Path:
        base = Path(__file__).resolve().parents[1] / "tmp_test_artifacts"
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"tmp_assets_{uuid.uuid4().hex[:8]}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_prepare_pre_pass_media_assets_uses_interval_spacing(self):
        root = self._make_temp_dir()
        video_path = root / "video.mp4"
        audio_path = root / "audio.ogg"

        with (
            patch(
                "services.translate.assets.MediaProcessor.get_media_duration",
                return_value=305.0,
            ),
            patch(
                "services.translate.assets.MediaProcessor.extract_video_frame"
            ) as extract_frame,
        ):
            assets = prepare_pre_pass_media_assets(
                video_path=video_path,
                audio_path=audio_path,
                cache_root=root / "pre_pass",
                interval_seconds=120,
                max_side=768,
                intro_skip_seconds=3.0,
            )

        self.assertEqual(
            [frame.timestamp_seconds for frame in assets.frames],
            [3.0, 120.0, 240.0, 303.5],
        )
        self.assertEqual(extract_frame.call_count, 4)
        self.assertEqual(assets.audio.path, audio_path)
        self.assertEqual(assets.audio.mime_type, "audio/ogg")
        self.assertTrue(assets.manifest_path.exists())
        manifest = json.loads(
            assets.manifest_path.read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["interval_seconds"], 120)
        self.assertEqual(manifest["frames"][0]["mime_type"], "image/jpeg")
        self.assertEqual(manifest["audio"]["path"], str(audio_path))

    def test_prepare_chunk_media_assets_samples_even_srt_starts(self):
        chunk = [
            SrtBlock(
                index=index + 1,
                timecode=(
                    f"{format_timecode(index * 15)} --> "
                    f"{format_timecode(index * 15 + (15 if index == 19 else 5))}"
                ),
                text=f"line {index + 1}",
            )
            for index in range(20)
        ]

        root = self._make_temp_dir()
        video_path = root / "video.mp4"
        audio_path = root / "audio.ogg"

        with (
            patch(
                "services.translate.assets.MediaProcessor.extract_audio_segment"
            ),
            patch(
                "services.translate.assets.MediaProcessor.extract_video_frame"
            ),
        ):
            assets = prepare_chunk_media_assets(
                video_path=video_path,
                audio_path=audio_path,
                cache_root=root / "chunks",
                video_key="video-key",
                chunk=chunk,
                chunk_index=1,
                total_chunks=2,
                interval_seconds=30,
                max_side=768,
                intro_skip_seconds=3.0,
            )

        self.assertEqual(
            [frame.timestamp_seconds for frame in assets.frames],
            [
                0.2,
                30.2,
                60.2,
                90.2,
                120.2,
                165.2,
                195.2,
                225.2,
                255.2,
                285.2,
            ],
        )
        manifest = json.loads(
            assets.manifest_path.read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["interval_seconds"], 30)
        self.assertIsNone(manifest["intro_skip_seconds"])
        self.assertEqual(manifest["max_side"], 768)
        self.assertEqual(manifest["audio"]["path"], str(assets.audio.path))
        self.assertEqual(manifest["frames"][0]["mime_type"], "image/jpeg")

    def test_prepare_chunk_media_assets_caps_frames_at_block_count(self):
        chunk = [
            SrtBlock(
                index=1,
                timecode="00:00:00,000 --> 00:00:01,000",
                text="a",
            ),
            SrtBlock(
                index=2,
                timecode="00:02:00,000 --> 00:02:01,000",
                text="b",
            ),
            SrtBlock(
                index=3,
                timecode="00:04:59,000 --> 00:05:00,000",
                text="c",
            ),
        ]

        root = self._make_temp_dir()
        video_path = root / "video.mp4"
        audio_path = root / "audio.ogg"

        with (
            patch(
                "services.translate.assets.MediaProcessor.extract_audio_segment"
            ),
            patch(
                "services.translate.assets.MediaProcessor.extract_video_frame"
            ),
        ):
            assets = prepare_chunk_media_assets(
                video_path=video_path,
                audio_path=audio_path,
                cache_root=root / "chunks",
                video_key="video-key",
                chunk=chunk,
                chunk_index=0,
                total_chunks=2,
                interval_seconds=30,
                max_side=768,
                intro_skip_seconds=3.0,
            )

        self.assertEqual(
            [frame.timestamp_seconds for frame in assets.frames],
            [0.2, 120.2, 299.2],
        )
        manifest = json.loads(
            assets.manifest_path.read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["interval_seconds"], 30)

    def test_first_chunk_uses_srt_start_instead_of_intro_skip_for_frames(self):
        chunk = [
            SrtBlock(
                index=1,
                timecode="00:00:00,500 --> 00:00:02,000",
                text="a",
            ),
            SrtBlock(
                index=2,
                timecode="00:01:59,000 --> 00:02:00,000",
                text="b",
            ),
        ]

        root = self._make_temp_dir()
        video_path = root / "video.mp4"
        audio_path = root / "audio.ogg"

        with (
            patch(
                "services.translate.assets.MediaProcessor.extract_audio_segment"
            ) as extract_audio,
            patch(
                "services.translate.assets.MediaProcessor.extract_video_frame"
            ),
        ):
            assets = prepare_chunk_media_assets(
                video_path=video_path,
                audio_path=audio_path,
                cache_root=root / "chunks",
                video_key="video-key",
                chunk=chunk,
                chunk_index=0,
                total_chunks=3,
                interval_seconds=60,
                max_side=768,
                intro_skip_seconds=3.0,
            )

        self.assertEqual(
            [frame.timestamp_seconds for frame in assets.frames],
            [0.7],
        )
        # Audio segment must NOT be shifted by intro_skip.
        self.assertEqual(
            extract_audio.call_args.kwargs["start_seconds"], 0.5
        )

    def test_chunk_frame_timestamp_clamps_to_range_end(self):
        chunk = [
            SrtBlock(
                index=10,
                timecode="00:00:09,950 --> 00:00:10,000",
                text="a",
            ),
        ]

        root = self._make_temp_dir()
        video_path = root / "video.mp4"
        audio_path = root / "audio.ogg"

        with (
            patch(
                "services.translate.assets.MediaProcessor.extract_audio_segment"
            ),
            patch(
                "services.translate.assets.MediaProcessor.extract_video_frame"
            ),
        ):
            assets = prepare_chunk_media_assets(
                video_path=video_path,
                audio_path=audio_path,
                cache_root=root / "chunks",
                video_key="video-key",
                chunk=chunk,
                chunk_index=0,
                total_chunks=3,
                interval_seconds=30,
                max_side=768,
                intro_skip_seconds=3.0,
            )

        self.assertEqual(
            [frame.timestamp_seconds for frame in assets.frames],
            [10.0],
        )

    def test_chunk_frame_interval_must_be_positive(self):
        chunk = [
            SrtBlock(
                index=1,
                timecode="00:00:00,000 --> 00:00:01,000",
                text="a",
            ),
        ]

        root = self._make_temp_dir()

        with self.assertRaises(ValueError):
            prepare_chunk_media_assets(
                video_path=root / "video.mp4",
                audio_path=root / "audio.ogg",
                cache_root=root / "chunks",
                video_key="video-key",
                chunk=chunk,
                chunk_index=0,
                total_chunks=1,
                interval_seconds=0,
                max_side=768,
            )

    def test_media_ref_to_part_reads_bytes_and_mime_type(self):
        root = self._make_temp_dir()
        media_path = root / "frame.jpg"
        media_path.write_bytes(b"image-bytes")

        part = media_ref_to_part(
            LocalMediaRef(path=media_path, mime_type="image/jpeg")
        )

        self.assertEqual(part.inline_data.data, b"image-bytes")
        self.assertEqual(part.inline_data.mime_type, "image/jpeg")

    def test_media_ref_to_part_raises_for_missing_file(self):
        root = self._make_temp_dir()

        with self.assertRaises(FileNotFoundError):
            media_ref_to_part(
                LocalMediaRef(
                    path=root / "missing.ogg",
                    mime_type="audio/ogg",
                )
            )


if __name__ == "__main__":
    unittest.main()
