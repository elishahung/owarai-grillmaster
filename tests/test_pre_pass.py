import json
import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("AGENT_GEMINI_API_KEY", "test-key")

from settings import ModelSpec
from services.translate.pre_pass import pre_pass as pp
from services.translate.assets import LocalMediaRef, PrePassMediaAssets
from services.inference.agy import AgyQuotaError
from services.translate.errors import PrePassError
from services.fixed_glossary import FixedGlossary
from services.srt import SrtBlock

_VALID_PREPASS_JSON = json.dumps(
    {
        "summary": "s",
        "characters": [],
        "proper_nouns": {},
        "glossary": {},
        "catchphrases": [],
        "tone_notes": "t",
        "segment_summaries": [],
    }
)


class PrePassSegmentCoverageTests(unittest.TestCase):
    def _result(self, *ranges):
        return pp.PrePassResult(
            summary="s",
            characters=[],
            proper_nouns={},
            glossary={},
            catchphrases=[],
            tone_notes="t",
            segment_summaries=[
                pp.SegmentSummary(from_index=f, to_index=t, summary="x")
                for f, t in ranges
            ],
        )

    def test_full_coverage_accepted(self):
        validate = pp._segment_coverage_validator([(1, 115), (116, 233)])
        validate(self._result((1, 115), (116, 233)))

    def test_missing_ranges_rejected_and_named(self):
        validate = pp._segment_coverage_validator([(1, 115), (116, 233)])
        with self.assertRaises(ValueError) as ctx:
            validate(self._result((1, 115)))
        message = str(ctx.exception)
        self.assertIn("1/2", message)
        # only the uncovered range is quoted back for repair
        self.assertIn('"from_index": 116', message)
        self.assertNotIn('"from_index": 1,', message)

    def test_shifted_range_does_not_count_as_coverage(self):
        validate = pp._segment_coverage_validator([(1, 115)])
        with self.assertRaises(ValueError):
            validate(self._result((1, 116)))

    def test_boundary_block_follows_the_srt(self):
        message = pp._build_user_message(
            video_title=None,
            video_description=None,
            translation_hint=None,
            source_metadata_context=None,
            parent_pre_pass_context=None,
            official_subtitle_context=None,
            fixed_glossary=FixedGlossary(),
            fixed_glossary_full=False,
            srt_text="SRT TEXT",
            boundaries=[(1, 115), (116, 233)],
            frame_timestamps=[],
        )
        self.assertLess(
            message.index("完整來源 SRT"), message.index("Chunk 邊界")
        )
        self.assertIn("必須剛好輸出 2 筆", message)

    def test_title_description_and_hint_are_separate_sections(self):
        message = pp._build_user_message(
            video_title="番組タイトル",
            video_description="企画の説明",
            translation_hint="第2回の続き",
            source_metadata_context=None,
            parent_pre_pass_context=None,
            official_subtitle_context=None,
            fixed_glossary=FixedGlossary(),
            fixed_glossary_full=False,
            srt_text="SRT TEXT",
            boundaries=[(1, 115)],
            frame_timestamps=[],
        )
        self.assertIn("【節目標題】\n番組タイトル", message)
        self.assertIn("【節目說明】\n企画の説明", message)
        self.assertIn("【使用者翻譯提示】\n第2回の続き", message)


class RunPrePassDispatchTests(unittest.TestCase):
    def _temp_dir(self) -> Path:
        base = Path(__file__).resolve().parents[1] / "tmp_test_artifacts"
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"tmp_pp_{uuid.uuid4().hex[:8]}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def _common_patches(self, tmp: Path, *, backend: str, audio: bool = True):
        audio_ref = (
            LocalMediaRef(path=tmp / "a.ogg", mime_type="audio/ogg")
            if audio
            else None
        )
        assets = PrePassMediaAssets(
            audio=audio_ref, frames=[], manifest_path=tmp / "assets.json"
        )
        for p in [
            patch.object(
                pp, "prepare_pre_pass_media_assets", return_value=assets
            ),
            patch.object(pp, "load_fixed_glossary", return_value=None),
            patch.object(pp, "filter_fixed_glossary", return_value=None),
            patch.object(
                pp, "format_fixed_glossary_block", return_value=""
            ),
            patch.object(
                pp.settings,
                "agent_prepass_model",
                ModelSpec(backend=backend, model="test-model"),
            ),
        ]:
            self.addCleanup(p.stop)
            p.start()

    def _run(self, tmp: Path):
        chunk = [
            SrtBlock(
                index=1,
                timecode="00:00:00,000 --> 00:00:02,000",
                text="hello",
            )
        ]
        return pp.run_pre_pass(
            srt_text="1\n00:00:00,000 --> 00:00:02,000\nhello\n",
            video_path=tmp / "v.mp4",
            audio_path=tmp / "a.ogg",
            chunks=[chunk],
            pre_pass_path=tmp / "pre_pass.json",
            pre_pass_cache_dir=tmp / "cache",
        )

    def _io(self, *, cost=0.0):
        from services.inference import InferenceResult

        return InferenceResult(
            text=_VALID_PREPASS_JSON, cost=cost, requests=1
        )

    def test_gemini_api_dispatch_writes_manifest(self):
        tmp = self._temp_dir()
        self._common_patches(tmp, backend="gemini-api")
        with patch.object(
            pp, "run_inference", return_value=self._io(cost=0.12)
        ) as mock_inf:
            result, cost = self._run(tmp)

        self.assertEqual(cost, 0.12)
        self.assertEqual(result.summary, "s")
        mock_inf.assert_called_once()
        self.assertIs(mock_inf.call_args.kwargs["schema"], pp.PrePassResult)
        self.assertEqual(
            mock_inf.call_args.kwargs["backend"], "gemini-api"
        )
        self.assertNotIn(
            "Use built-in web search only",
            mock_inf.call_args.kwargs["system_prompt"],
        )
        # gemini-api supports audio, so the audio file is passed through.
        self.assertEqual(len(mock_inf.call_args.kwargs["audio"]), 1)
        manifest = json.loads(
            (tmp / "cache" / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["backend"], "gemini-api")
        self.assertTrue((tmp / "pre_pass.json").exists())

    def test_agy_dispatch_writes_manifest(self):
        tmp = self._temp_dir()
        self._common_patches(tmp, backend="agy")
        with patch.object(
            pp, "run_inference", return_value=self._io()
        ) as mock_inf:
            result, cost = self._run(tmp)

        self.assertEqual(cost, 0.0)
        self.assertEqual(result.summary, "s")
        self.assertIs(mock_inf.call_args.kwargs["schema"], pp.PrePassResult)
        self.assertIn(
            "Use built-in web search only",
            mock_inf.call_args.kwargs["system_prompt"],
        )
        self.assertIn(
            "The SRT is not ground truth",
            mock_inf.call_args.kwargs["system_prompt"],
        )
        manifest = json.loads(
            (tmp / "cache" / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["backend"], "agy")
        # agy hears audio, so the track is passed through.
        self.assertEqual(len(mock_inf.call_args.kwargs["audio"]), 1)

    def test_validator_rejects_incomplete_segment_coverage(self):
        tmp = self._temp_dir()
        self._common_patches(tmp, backend="agy")
        with patch.object(
            pp, "run_inference", return_value=self._io()
        ) as mock_inf:
            self._run(tmp)

        # _run supplies one chunk covering index 1..1; the stub response has no
        # segment_summaries at all, so the validator handed to run_inference
        # must reject it and drive a repair round.
        validate = mock_inf.call_args.kwargs["validate"]
        with self.assertRaises(ValueError):
            validate(pp.PrePassResult.model_validate_json(_VALID_PREPASS_JSON))

    def test_agent_backend_drops_audio(self):
        tmp = self._temp_dir()
        # Agent backend: prepare_pre_pass_media_assets returns no audio.
        self._common_patches(tmp, backend="claude", audio=False)
        with patch.object(
            pp, "run_inference", return_value=self._io()
        ) as mock_inf:
            self._run(tmp)

        self.assertIsNone(mock_inf.call_args.kwargs["audio"])
        # The system instruction is rendered without audio claims.
        self.assertNotIn(
            "Full Source Audio", mock_inf.call_args.kwargs["system_prompt"]
        )

    def test_quota_error_becomes_prepass_error(self):
        tmp = self._temp_dir()
        self._common_patches(tmp, backend="agy")
        with patch.object(
            pp,
            "run_inference",
            side_effect=AgyQuotaError("quota will reset after 8h"),
        ) as mock_inf:
            with self.assertRaises(PrePassError) as ctx:
                self._run(tmp)

        self.assertEqual(mock_inf.call_count, 1)
        self.assertIn("quota", str(ctx.exception).lower())
        self.assertEqual(ctx.exception.accumulated_cost, 0.0)

    def test_claude_rate_limit_is_a_quota_error(self):
        # Every backend's quota error shares InferenceQuotaError, so the stage
        # maps a Claude 429 exactly like an agy quota hit.
        from services.inference import InferenceQuotaError
        from services.inference.claude_sdk import ClaudeSDKRateLimitError

        self.assertTrue(issubclass(ClaudeSDKRateLimitError, InferenceQuotaError))
        self.assertTrue(issubclass(AgyQuotaError, InferenceQuotaError))
        tmp = self._temp_dir()
        self._common_patches(tmp, backend="claude", audio=False)
        with patch.object(
            pp,
            "run_inference",
            side_effect=ClaudeSDKRateLimitError("resets at 5pm"),
        ):
            with self.assertRaises(PrePassError) as ctx:
                self._run(tmp)
        self.assertIn("claude quota exhausted", str(ctx.exception))

    def test_failure_raises_prepass_error(self):
        tmp = self._temp_dir()
        self._common_patches(tmp, backend="gemini-api")
        with patch.object(
            pp, "run_inference", side_effect=RuntimeError("genai boom")
        ) as mock_inf:
            with self.assertRaises(PrePassError):
                self._run(tmp)
        self.assertEqual(mock_inf.call_count, 1)


if __name__ == "__main__":
    unittest.main()
