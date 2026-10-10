import json
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


from grillmaster.settings import ModelSpec
from grillmaster.services.translate.pre_pass import pre_pass as pp
from grillmaster.services.translate.assets import PrePassMediaAssets
from grillmaster.services.inference.agy import AgyQuotaError
from grillmaster.services.fixed_glossary import FixedGlossary
from grillmaster.services.srt import SrtBlock

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
            srt_text="SRT TEXT",
            boundaries=[(1, 115), (116, 233)],
            frame_timestamps=[],
        )
        self.assertLess(message.index("完整來源 SRT"), message.index("Chunk 邊界"))
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
        assets = PrePassMediaAssets(
            audio=tmp / "a.ogg" if audio else None,
            frames=[],
            manifest_path=tmp / "assets.json",
        )
        for p in [
            patch.object(pp, "prepare_pre_pass_media_assets", return_value=assets),
            patch.object(pp, "load_fixed_glossary", return_value=FixedGlossary()),
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

    def _result(self):
        return pp.PrePassResult.model_validate_json(_VALID_PREPASS_JSON)

    def test_agy_dispatch_writes_manifest(self):
        tmp = self._temp_dir()
        self._common_patches(tmp, backend="agy")
        with patch.object(pp, "run_inference", return_value=self._result()) as mock_inf:
            result = self._run(tmp)

        self.assertEqual(result.summary, "s")
        kwargs = mock_inf.call_args.kwargs
        self.assertIs(kwargs["schema"], pp.PrePassResult)
        self.assertIn("Use built-in web search only", kwargs["prompt"])
        self.assertIn("The SRT is not ground truth", kwargs["prompt"])
        # Every backend is an agent: the frame tool is always offered.
        self.assertIn("get_frames_for_pre_pass.py", kwargs["prompt"])
        manifest = json.loads(
            (tmp / "cache" / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["model"], "agy/test-model/high")
        # agy hears audio, so the track is passed through.
        self.assertEqual(kwargs["audio"], [tmp / "a.ogg"])
        self.assertTrue((tmp / "pre_pass.json").exists())

    def test_existing_pre_pass_is_reused_before_any_media_work(self):
        tmp = self._temp_dir()
        self._common_patches(tmp, backend="agy")
        (tmp / "pre_pass.json").write_text(_VALID_PREPASS_JSON, encoding="utf-8")
        with patch.object(pp, "run_inference") as mock_inf:
            result = self._run(tmp)

        self.assertEqual(result.summary, "s")
        mock_inf.assert_not_called()
        pp.prepare_pre_pass_media_assets.assert_not_called()

    def test_validator_rejects_incomplete_segment_coverage(self):
        tmp = self._temp_dir()
        self._common_patches(tmp, backend="agy")
        with patch.object(pp, "run_inference", return_value=self._result()) as mock_inf:
            self._run(tmp)

        # _run supplies one chunk covering index 1..1; the stub response has no
        # segment_summaries at all, so the validator handed to run_inference
        # must reject it and drive a repair round.
        validate = mock_inf.call_args.kwargs["validate"]
        with self.assertRaises(ValueError):
            validate(self._result())

    def test_audio_incapable_backend_drops_audio(self):
        tmp = self._temp_dir()
        # claude: prepare_pre_pass_media_assets returns no audio.
        self._common_patches(tmp, backend="claude", audio=False)
        with patch.object(pp, "run_inference", return_value=self._result()) as mock_inf:
            self._run(tmp)

        self.assertIsNone(mock_inf.call_args.kwargs["audio"])
        # The instruction is rendered without audio claims.
        self.assertNotIn("Full Source Audio", mock_inf.call_args.kwargs["prompt"])

    def test_failure_propagates_without_writing_pre_pass(self):
        tmp = self._temp_dir()
        self._common_patches(tmp, backend="agy")
        with patch.object(
            pp,
            "run_inference",
            side_effect=AgyQuotaError("quota will reset after 8h"),
        ):
            with self.assertRaises(AgyQuotaError):
                self._run(tmp)
        self.assertFalse((tmp / "pre_pass.json").exists())


if __name__ == "__main__":
    unittest.main()
