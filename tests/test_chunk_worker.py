import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

from settings import ModelSpec
from services.inference.agy import AgyQuotaError
import services.translate.chunk.chunk_worker as cw
from services.translate.assets import ChunkMediaAssets, FrameSpec
from services.translate.chunk.chunk_worker import translate_chunk
from services.translate.pre_pass.schema import PrePassResult, SegmentSummary
from services.media import TimeRange
from services.srt import SrtBlock

_CHUNK = [
    SrtBlock(
        index=1,
        timecode="00:00:01,000 --> 00:00:02,000",
        text="source",
    )
]
_TRANSLATED = "1\n00:00:01,000 --> 00:00:02,000\ntranslated\n"


class ChunkDispatchTests(unittest.IsolatedAsyncioTestCase):
    """translate_chunk routes media + system prompt through run_inference."""

    def _make_temp_dir(self) -> Path:
        base = Path(__file__).resolve().parents[1] / "tmp_test_artifacts"
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"tmp_chunk_{uuid.uuid4().hex[:8]}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def _assets(self, root: Path, *, audio: bool = True) -> ChunkMediaAssets:
        audio_path = root / "chunk.ogg"
        frame_path = root / "frame.jpg"
        audio_path.write_bytes(b"chunk-audio")
        frame_path.write_bytes(b"chunk-frame")
        assets = ChunkMediaAssets(
            video_path=root / "video.mp4",
            time_range=TimeRange(start_seconds=1.0, end_seconds=2.0),
            audio=audio_path if audio else None,
            frames=[FrameSpec(path=frame_path, timestamp_seconds=1.0)],
            manifest_path=root / "chunk.json",
            response_dir=root,
        )
        assets.manifest_path.write_text("{}", encoding="utf-8")
        return assets

    def _pre_pass(self) -> PrePassResult:
        return PrePassResult(
            summary="summary",
            characters=[],
            proper_nouns={},
            glossary={},
            catchphrases=[],
            tone_notes="tone",
            segment_summaries=[SegmentSummary(from_index=1, to_index=1, summary="seg")],
        )

    def _patch_backend(self, backend: str):
        # Pin the backend so the test is independent of the developer's .env.
        p = patch.object(
            cw.settings,
            "agent_chunk_model",
            ModelSpec(backend=backend, model="test-model"),
        )
        self.addCleanup(p.stop)
        p.start()

    async def test_agy_gets_audio_frames_and_frame_tool(self):
        assets = self._assets(self._make_temp_dir())
        self._patch_backend("agy")
        with patch.object(cw, "run_inference", return_value=_TRANSLATED) as mock_inf:
            result = await translate_chunk(assets, _CHUNK, 0, 1, self._pre_pass())

        self.assertEqual(result.blocks[0].text, "translated")
        kwargs = mock_inf.call_args.kwargs
        self.assertEqual(kwargs["images"], [assets.frames[0].path])
        self.assertEqual(kwargs["audio"], [assets.audio])
        # Every backend is an agent: the frame-tool block, scoped to the
        # chunk's time range, is always appended.
        prompt = kwargs["prompt"]
        self.assertIn("On-demand video frames", prompt)
        self.assertIn("your assigned chunk range", prompt)
        self.assertIn("get_frames_for_chunk.py", prompt)

    async def test_chunk_without_audio_renders_no_audio_instruction(self):
        # The facade extracts no chunk audio for an audio-incapable backend
        # (claude), so the worker attaches none and drops the audio claims.
        assets = self._assets(self._make_temp_dir(), audio=False)
        self._patch_backend("claude")
        with patch.object(cw, "run_inference", return_value=_TRANSLATED) as mock_inf:
            await translate_chunk(assets, _CHUNK, 0, 1, self._pre_pass())

        kwargs = mock_inf.call_args.kwargs
        self.assertIsNone(kwargs["audio"])
        self.assertIn("no audio is available for this run", kwargs["prompt"])
        self.assertIn("On-demand video frames", kwargs["prompt"])

    async def test_raw_validation_failure_goes_directly_to_fix_layer(self):
        assets = self._assets(self._make_temp_dir())
        self._patch_backend("agy")
        raw_srt = "7\n99:99:99,999 --> 99:99:99,999\ntranslated\n"
        with (
            patch.object(cw, "run_inference", return_value=raw_srt),
            patch.object(
                cw,
                "fix_chunk_structure",
                new=AsyncMock(return_value=_TRANSLATED),
            ) as mock_fix,
        ):
            result = await translate_chunk(assets, _CHUNK, 0, 1, self._pre_pass())

        mock_fix.assert_awaited_once()
        self.assertEqual(result.blocks[0].text, "translated")

    async def test_quota_error_fails_the_chunk_without_retrying(self):
        assets = self._assets(self._make_temp_dir())
        self._patch_backend("agy")
        with patch.object(
            cw, "run_inference", side_effect=AgyQuotaError("quota spent")
        ) as mock_inf:
            with self.assertRaises(AgyQuotaError):
                await translate_chunk(assets, _CHUNK, 0, 1, self._pre_pass())
        mock_inf.assert_called_once()

    async def test_other_failures_retry_after_a_fixed_pause(self):
        assets = self._assets(self._make_temp_dir())
        self._patch_backend("agy")
        with (
            patch.object(cw.settings, "chunk_max_retries", 3),
            patch.object(
                cw,
                "run_inference",
                side_effect=[RuntimeError("blip"), RuntimeError("blip"), _TRANSLATED],
            ) as mock_inf,
            patch.object(cw.asyncio, "sleep", new=AsyncMock()) as mock_sleep,
        ):
            result = await translate_chunk(assets, _CHUNK, 0, 1, self._pre_pass())
        self.assertEqual(mock_inf.call_count, 3)
        self.assertEqual(result.retries, 2)
        self.assertEqual(
            [c.args[0] for c in mock_sleep.await_args_list],
            [cw._RETRY_DELAY_SECONDS] * 2,
        )


if __name__ == "__main__":
    unittest.main()
