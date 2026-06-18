import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from google import genai

from services.gemini.assets import (
    ChunkMediaAssets,
    FrameSpec,
    LocalMediaRef,
    PrePassMediaAssets,
)
import services.gemini.chunk_worker as cw
from services.gemini.chunk_worker import translate_chunk
from services.gemini.cli import GeminiCliResult
from services.srt import SrtBlock
from services.gemini.pre_pass import (
    Catchphrase,
    Character,
    PrePassResult,
    SegmentSummary,
    run_pre_pass,
)
from services.media import TimeRange


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

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _FakeAio:
    def __init__(self, models: _FakeModels):
        self.models = models


class _FakeClient:
    def __init__(self, response: _FakeResponse):
        self.models = _FakeModels(response)
        self.aio = _FakeAio(self.models)


class GeminiInlineMediaTests(unittest.IsolatedAsyncioTestCase):
    def _make_temp_dir(self) -> Path:
        base = Path(__file__).resolve().parents[1] / "tmp_test_artifacts"
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"tmp_inline_{uuid.uuid4().hex[:8]}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    async def test_pre_pass_sends_inline_media_parts(self):
        root = self._make_temp_dir()
        audio_path = root / "full.ogg"
        frame_path = root / "frame.jpg"
        asset_manifest = root / "assets.json"
        audio_path.write_bytes(b"audio-bytes")
        frame_path.write_bytes(b"frame-bytes")

        result = PrePassResult(
            summary="summary",
            characters=[
                Character(name_jp="JP", name_zh="ZH", role_note="role")
            ],
            proper_nouns={},
            glossary={},
            catchphrases=[
                Catchphrase(phrase_jp="jp", phrase_zh="zh", note="note")
            ],
            tone_notes="tone",
            segment_summaries=[
                SegmentSummary(from_index=1, to_index=1, summary="segment")
            ],
        )
        client = _FakeClient(_FakeResponse(result.model_dump_json()))
        chunks = [
            [
                SrtBlock(
                    index=1,
                    timecode="00:00:01,000 --> 00:00:02,000",
                    text="source",
                )
            ]
        ]
        assets = PrePassMediaAssets(
            audio=LocalMediaRef(path=audio_path, mime_type="audio/ogg"),
            frames=[
                FrameSpec(
                    path=frame_path,
                    timestamp_seconds=1.0,
                    mime_type="image/jpeg",
                )
            ],
            manifest_path=asset_manifest,
        )

        with (
            patch(
                "services.gemini.pre_pass.prepare_pre_pass_media_assets",
                return_value=assets,
            ),
            patch(
                "services.gemini.pre_pass.settings.prepass_gemini_backend",
                "api",
            ),
        ):
            parsed, cost = await run_pre_pass(
                client,
                "description",
                "1\n00:00:01,000 --> 00:00:02,000\nsource",
                root / "video.mp4",
                audio_path,
                chunks,
                root / "pre_pass.json",
                root,
                "Official source cast/talent metadata:\n- 山内　健司",
            )

        contents = client.models.calls[0]["contents"]
        config = client.models.calls[0]["config"]
        self.assertEqual(cost, 0.0)
        self.assertEqual(parsed.summary, "summary")
        self.assertEqual(contents[0].inline_data.data, b"audio-bytes")
        self.assertEqual(contents[0].inline_data.mime_type, "audio/ogg")
        self.assertEqual(contents[1].inline_data.data, b"frame-bytes")
        self.assertEqual(contents[1].inline_data.mime_type, "image/jpeg")
        self.assertIsInstance(contents[-1], str)
        self.assertIn("【官方來源 Metadata】", contents[-1])
        self.assertIn("山内　健司", contents[-1])
        self.assertIn("OFFICIAL SOURCE METADATA", config.system_instruction)
        self.assertIn("characters` MUST include", config.system_instruction)
        self.assertIn("exactly as written", config.system_instruction)

    async def test_chunk_worker_sends_inline_media_parts(self):
        root = self._make_temp_dir()
        audio_path = root / "chunk.ogg"
        frame_path = root / "frame.jpg"
        audio_path.write_bytes(b"chunk-audio")
        frame_path.write_bytes(b"chunk-frame")
        response_text = "1\n" "00:00:01,000 --> 00:00:02,000\n" "translated\n"
        client = _FakeClient(
            _FakeResponse(response_text, genai.types.FinishReason.STOP)
        )
        chunk = [
            SrtBlock(
                index=1,
                timecode="00:00:01,000 --> 00:00:02,000",
                text="source",
            )
        ]
        pre_pass = PrePassResult(
            summary="summary",
            characters=[],
            proper_nouns={},
            glossary={},
            catchphrases=[],
            tone_notes="tone",
            segment_summaries=[
                SegmentSummary(from_index=1, to_index=1, summary="segment")
            ],
        )
        media_assets = ChunkMediaAssets(
            time_range=TimeRange(start_seconds=1.0, end_seconds=2.0),
            audio=LocalMediaRef(path=audio_path, mime_type="audio/ogg"),
            frames=[
                FrameSpec(
                    path=frame_path,
                    timestamp_seconds=1.0,
                    mime_type="image/jpeg",
                )
            ],
            manifest_path=root / "chunk.json",
            response_dir=root,
        )
        media_assets.manifest_path.write_text("{}", encoding="utf-8")

        result = await translate_chunk(
            client,
            media_assets,
            chunk,
            0,
            1,
            pre_pass,
        )

        contents = client.models.calls[0]["contents"]
        self.assertEqual(result.blocks[0].text, "translated")
        self.assertEqual(contents[0].inline_data.data, b"chunk-audio")
        self.assertEqual(contents[0].inline_data.mime_type, "audio/ogg")
        self.assertEqual(contents[1].inline_data.data, b"chunk-frame")
        self.assertEqual(contents[1].inline_data.mime_type, "image/jpeg")
        self.assertIsInstance(contents[-1], str)

    async def test_chunk_worker_cli_backend(self):
        root = self._make_temp_dir()
        audio_path = root / "chunk.ogg"
        frame_path = root / "frame.jpg"
        audio_path.write_bytes(b"chunk-audio")
        frame_path.write_bytes(b"chunk-frame")
        cli_srt = "1\n00:00:01,000 --> 00:00:02,000\ntranslated\n"
        chunk = [
            SrtBlock(
                index=1,
                timecode="00:00:01,000 --> 00:00:02,000",
                text="source",
            )
        ]
        pre_pass = PrePassResult(
            summary="summary",
            characters=[],
            proper_nouns={},
            glossary={},
            catchphrases=[],
            tone_notes="tone",
            segment_summaries=[
                SegmentSummary(from_index=1, to_index=1, summary="segment")
            ],
        )
        media_assets = ChunkMediaAssets(
            time_range=TimeRange(start_seconds=1.0, end_seconds=2.0),
            audio=LocalMediaRef(path=audio_path, mime_type="audio/ogg"),
            frames=[
                FrameSpec(
                    path=frame_path,
                    timestamp_seconds=1.0,
                    mime_type="image/jpeg",
                )
            ],
            manifest_path=root / "chunk.json",
            response_dir=root,
        )
        media_assets.manifest_path.write_text("{}", encoding="utf-8")

        cli_result = GeminiCliResult(
            response=cli_srt, requests=1, stats={}, raw_envelope={}
        )
        # client is None: the cli backend must never touch the api client.
        with (
            patch.object(cw.settings, "chunk_gemini_backend", "cli"),
            patch.object(
                cw, "run_gemini_cli", return_value=cli_result
            ) as mock_cli,
        ):
            result = await translate_chunk(
                None, media_assets, chunk, 0, 1, pre_pass
            )

        self.assertEqual(result.blocks[0].text, "translated")
        self.assertEqual(result.cost, 0.0)
        mock_cli.assert_called_once()
        self.assertEqual(
            mock_cli.call_args.kwargs["model"], cw.settings.chunk_gemini_model
        )
        self.assertIsNone(mock_cli.call_args.kwargs["schema"])
        media_files = mock_cli.call_args.kwargs["media_files"]
        self.assertEqual(media_files[0], audio_path)
        self.assertIn(frame_path, media_files)


if __name__ == "__main__":
    unittest.main()
