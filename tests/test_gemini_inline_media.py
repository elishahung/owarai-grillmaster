import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from google import genai

from services.inference import InferenceResult
import services.inference.gemini_api as gemini_api
from services.inference.gemini_api import run_gemini_api
import services.translate.chunk.chunk_worker as cw
from services.translate.assets import ChunkMediaAssets, FrameSpec, LocalMediaRef
from services.translate.chunk.chunk_worker import translate_chunk
from services.translate.pre_pass.schema import PrePassResult, SegmentSummary
from services.media import TimeRange
from services.srt import SrtBlock


class _FakeResponse:
    def __init__(self, text: str, finish_reason=None):
        self.text = text
        self.usage_metadata = None
        self.candidates = []
        if finish_reason is not None:
            self.candidates = [
                type("Candidate", (), {"finish_reason": finish_reason})()
            ]


class _FakeModels:
    def __init__(self, response: _FakeResponse):
        self.response = response
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _FakeClient:
    def __init__(self, response: _FakeResponse):
        self.models = _FakeModels(response)


class GeminiApiInlineMediaTests(unittest.TestCase):
    """The gemini-api backend builds inline audio+frame Parts then the prompt."""

    def _make_temp_dir(self) -> Path:
        base = Path(__file__).resolve().parents[1] / "tmp_test_artifacts"
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"tmp_inline_{uuid.uuid4().hex[:8]}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_run_gemini_api_sends_audio_then_frames_then_prompt(self):
        root = self._make_temp_dir()
        audio_path = root / "full.ogg"
        frame_path = root / "frame.jpg"
        audio_path.write_bytes(b"audio-bytes")
        frame_path.write_bytes(b"frame-bytes")

        result = PrePassResult(
            summary="summary",
            characters=[],
            proper_nouns={},
            glossary={},
            catchphrases=[],
            tone_notes="tone",
            segment_summaries=[
                SegmentSummary(from_index=1, to_index=1, summary="seg")
            ],
        )
        client = _FakeClient(
            _FakeResponse(
                result.model_dump_json(), genai.types.FinishReason.STOP
            )
        )
        with patch.object(gemini_api, "_api_client", return_value=client):
            io = run_gemini_api(
                prompt="USER",
                system_prompt="SYSTEM",
                images=[frame_path],
                audio=[audio_path],
                schema=PrePassResult,
                model="gemini-3.1-pro-preview",
            )

        self.assertEqual(io.cost, 0.0)  # usage_metadata None -> 0
        parsed = PrePassResult.model_validate_json(io.text)
        self.assertEqual(parsed.summary, "summary")

        contents = client.models.calls[0]["contents"]
        config = client.models.calls[0]["config"]
        self.assertEqual(contents[0].inline_data.data, b"audio-bytes")
        self.assertEqual(contents[0].inline_data.mime_type, "audio/ogg")
        self.assertEqual(contents[1].inline_data.data, b"frame-bytes")
        self.assertEqual(contents[1].inline_data.mime_type, "image/jpeg")
        self.assertEqual(contents[-1], "USER")
        self.assertEqual(config.system_instruction, "SYSTEM")


class ChunkDispatchTests(unittest.IsolatedAsyncioTestCase):
    """translate_chunk routes media + system prompt through run_inference."""

    def _make_temp_dir(self) -> Path:
        base = Path(__file__).resolve().parents[1] / "tmp_test_artifacts"
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"tmp_chunk_{uuid.uuid4().hex[:8]}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def _assets(self, root: Path) -> ChunkMediaAssets:
        audio_path = root / "chunk.ogg"
        frame_path = root / "frame.jpg"
        audio_path.write_bytes(b"chunk-audio")
        frame_path.write_bytes(b"chunk-frame")
        assets = ChunkMediaAssets(
            video_path=root / "video.mp4",
            time_range=TimeRange(start_seconds=1.0, end_seconds=2.0),
            audio=LocalMediaRef(path=audio_path, mime_type="audio/ogg"),
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
            segment_summaries=[
                SegmentSummary(from_index=1, to_index=1, summary="seg")
            ],
        )

    async def test_chunk_passes_media_and_no_schema(self):
        root = self._make_temp_dir()
        assets = self._assets(root)
        chunk = [
            SrtBlock(
                index=1,
                timecode="00:00:01,000 --> 00:00:02,000",
                text="source",
            )
        ]
        raw_srt = "1\n00:00:01,000 --> 00:00:02,000\ntranslated\n"

        # Pin the backend so the test is independent of the developer's .env.
        with (
            patch.object(cw.settings, "agent_chunk_backend", "gemini-api"),
            patch.object(
                cw,
                "run_inference",
                return_value=InferenceResult(
                    text=raw_srt, cost=0.0, requests=1
                ),
            ) as mock_inf,
        ):
            result = await translate_chunk(assets, chunk, 0, 1, self._pre_pass())

        self.assertEqual(result.blocks[0].text, "translated")
        kwargs = mock_inf.call_args.kwargs
        self.assertIsNone(kwargs["schema"])
        self.assertEqual(len(kwargs["images"]), 1)
        self.assertEqual(len(kwargs["audio"]), 1)  # gemini-api keeps audio
        # gemini-api is not an agent backend -> no frame-tool block appended.
        self.assertNotIn("On-demand video frames", kwargs["system_prompt"])

    async def test_agent_backend_appends_frame_tool_instruction(self):
        root = self._make_temp_dir()
        assets = self._assets(root)
        chunk = [
            SrtBlock(
                index=1,
                timecode="00:00:01,000 --> 00:00:02,000",
                text="source",
            )
        ]
        raw_srt = "1\n00:00:01,000 --> 00:00:02,000\ntranslated\n"

        # An agent backend (claude) gets the on-demand frame-tool block, scoped
        # to the chunk's time range. run_inference is mocked, so no real CLI /
        # ffmpeg runs and the video file need not exist.
        with (
            patch.object(cw.settings, "agent_chunk_backend", "claude"),
            patch.object(
                cw,
                "run_inference",
                return_value=InferenceResult(
                    text=raw_srt, cost=0.0, requests=1
                ),
            ) as mock_inf,
        ):
            await translate_chunk(assets, chunk, 0, 1, self._pre_pass())

        system_prompt = mock_inf.call_args.kwargs["system_prompt"]
        self.assertIn("On-demand video frames", system_prompt)
        self.assertIn("your assigned chunk range", system_prompt)
        self.assertIn("get_frames.py", system_prompt)


if __name__ == "__main__":
    unittest.main()
