import asyncio
import json
import os
import shutil
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("GEMINI_API_KEY", "test-key")

from pydantic import BaseModel

from services.gemini import cli as cli_mod
from services.gemini import pre_pass as pp
from services.gemini.assets import LocalMediaRef, PrePassMediaAssets
from services.gemini.cli import (
    GeminiCliError,
    GeminiCliNotInstalledError,
    GeminiCliQuotaError,
    GeminiCliResult,
    extract_json_object,
    extract_request_count,
    run_gemini_cli,
)
from services.gemini.errors import PrePassError
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


def _prepass() -> "pp.PrePassResult":
    return pp.PrePassResult.model_validate_json(_VALID_PREPASS_JSON)


class _Demo(BaseModel):
    a: int


def _completed(response, *, returncode=0, total_requests=1):
    envelope = {
        "response": response,
        "stats": {"models": {"m": {"api": {"totalRequests": total_requests}}}},
        "error": None,
    }
    return SimpleNamespace(
        returncode=returncode, stdout=json.dumps(envelope), stderr=""
    )


class ExtractJsonObjectTests(unittest.TestCase):
    def test_clean_object_passthrough(self):
        self.assertEqual(extract_json_object('{"a": 1}'), '{"a": 1}')

    def test_strips_json_fence(self):
        self.assertEqual(
            extract_json_object('```json\n{"a": 1}\n```'), '{"a": 1}'
        )

    def test_strips_bare_fence(self):
        self.assertEqual(
            extract_json_object('```\n{"a": 1}\n```'), '{"a": 1}'
        )

    def test_unwraps_surrounding_prose(self):
        self.assertEqual(
            extract_json_object('Here you go:\n{"a": 1}\nThanks!'),
            '{"a": 1}',
        )

    def test_no_braces_returns_stripped_input(self):
        self.assertEqual(extract_json_object("  no json  "), "no json")


class ExtractRequestCountTests(unittest.TestCase):
    def test_documented_path(self):
        env = {"stats": {"models": {"m": {"api": {"totalRequests": 3}}}}}
        self.assertEqual(extract_request_count(env), 3)

    def test_multi_model_sum(self):
        env = {
            "stats": {
                "models": {
                    "a": {"api": {"totalRequests": 2}},
                    "b": {"api": {"totalRequests": 4}},
                }
            }
        }
        self.assertEqual(extract_request_count(env), 6)

    def test_missing_stats_floor_is_one(self):
        self.assertEqual(extract_request_count({}), 1)

    def test_stats_without_total_requests_floor_is_one(self):
        self.assertEqual(
            extract_request_count({"stats": {"models": {"m": {}}}}), 1
        )

    def test_recursive_fallback(self):
        env = {"foo": {"bar": {"totalRequests": 5}}}
        self.assertEqual(extract_request_count(env), 5)


class RunGeminiCliTests(unittest.TestCase):
    def _temp_dir(self) -> Path:
        base = Path(__file__).resolve().parents[1] / "tmp_test_artifacts"
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"tmp_cli_{uuid.uuid4().hex[:8]}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def _patch_which(self):
        p = patch.object(cli_mod.shutil, "which", return_value="gemini")
        self.addCleanup(p.stop)
        p.start()

    def _patch_run(self, **kwargs):
        p = patch.object(cli_mod.subprocess, "run", **kwargs)
        self.addCleanup(p.stop)
        return p.start()

    def test_not_installed(self):
        with patch.object(cli_mod.shutil, "which", return_value=None):
            with self.assertRaises(GeminiCliNotInstalledError):
                run_gemini_cli("hi", model="m")

    def test_success_no_schema(self):
        self._patch_which()
        mock_run = self._patch_run(
            return_value=_completed("answer", total_requests=2)
        )
        result = run_gemini_cli("hi", model="m")
        self.assertIsInstance(result, GeminiCliResult)
        self.assertEqual(result.response, "answer")
        self.assertEqual(result.requests, 2)
        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs["input"], "hi")
        self.assertNotIn("GEMINI_API_KEY", kwargs["env"])

    def test_quota_error_classified(self):
        self._patch_which()
        envelope = {
            "response": None,
            "stats": {},
            "error": {"code": 429, "message": "quota will reset after 8h"},
        }
        self._patch_run(
            return_value=SimpleNamespace(
                returncode=1, stdout=json.dumps(envelope), stderr=""
            )
        )
        with self.assertRaises(GeminiCliQuotaError):
            run_gemini_cli("hi", model="m")

    def test_non_quota_error_classified(self):
        self._patch_which()
        envelope = {
            "response": None,
            "stats": {},
            "error": {"code": 500, "message": "internal server error"},
        }
        self._patch_run(
            return_value=SimpleNamespace(
                returncode=1, stdout=json.dumps(envelope), stderr=""
            )
        )
        with self.assertRaises(GeminiCliError) as ctx:
            run_gemini_cli("hi", model="m")
        self.assertNotIsInstance(ctx.exception, GeminiCliQuotaError)

    def test_oversized_media_hard_error(self):
        self._patch_which()
        tmp = self._temp_dir()
        media = tmp / "big.ogg"
        media.write_bytes(b"x" * 16)
        with patch.object(cli_mod, "_MAX_MEDIA_FILE_MB", 0):
            with self.assertRaises(GeminiCliError) as ctx:
                run_gemini_cli("hi", model="m", media_files=[media])
        self.assertIn("exceeds", str(ctx.exception))

    def test_empty_media_is_valid(self):
        self._patch_which()
        self._patch_run(return_value=_completed("ok"))
        result = run_gemini_cli("hi", model="m", media_files=[])
        self.assertEqual(result.response, "ok")
        self.assertEqual(result.requests, 1)

    def test_media_staged_with_relative_tokens_and_cwd(self):
        self._patch_which()
        tmp = self._temp_dir()
        media = tmp / "frame.jpg"
        media.write_bytes(b"jpegbytes")
        mock_run = self._patch_run(return_value=_completed("ok"))
        run_gemini_cli("analyze", model="m", media_files=[media])
        _, kwargs = mock_run.call_args
        self.assertIsNotNone(kwargs["cwd"])
        self.assertNotEqual(kwargs["cwd"], str(tmp))
        self.assertIn("@00_frame.jpg", kwargs["input"])
        self.assertNotIn(str(media), kwargs["input"])
        self.assertIn("--skip-trust", mock_run.call_args.args[0])

    def test_schema_success_first_try(self):
        self._patch_which()
        mock_run = self._patch_run(return_value=_completed('{"a": 1}'))
        result = run_gemini_cli("hi", model="m", schema=_Demo)
        self.assertEqual(mock_run.call_count, 1)
        self.assertEqual(result.response, '{"a": 1}')
        # The JSON Schema instruction is appended to the prompt.
        self.assertIn("JSON Schema", mock_run.call_args.kwargs["input"])

    def test_schema_repair_retries_then_succeeds(self):
        self._patch_which()
        mock_run = self._patch_run(
            side_effect=[
                _completed('{"a": "not-int"}'),
                _completed('{"a": 7}'),
            ]
        )
        result = run_gemini_cli("hi", model="m", schema=_Demo)
        self.assertEqual(mock_run.call_count, 2)
        self.assertEqual(result.response, '{"a": 7}')
        self.assertEqual(result.requests, 2)  # summed across attempts
        first_input = mock_run.call_args_list[0].kwargs["input"]
        second_input = mock_run.call_args_list[1].kwargs["input"]
        self.assertNotIn("修正要求", first_input)
        self.assertIn("修正要求", second_input)

    def test_schema_exceeds_retries_raises(self):
        self._patch_which()
        mock_run = self._patch_run(
            side_effect=[_completed('{"a": "bad"}') for _ in range(3)]
        )
        with self.assertRaises(GeminiCliError):
            run_gemini_cli("hi", model="m", schema=_Demo, max_retries=3)
        self.assertEqual(mock_run.call_count, 3)


class RunPrePassDispatchTests(unittest.TestCase):
    def _temp_dir(self) -> Path:
        base = Path(__file__).resolve().parents[1] / "tmp_test_artifacts"
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"tmp_pp_{uuid.uuid4().hex[:8]}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def _common_patches(self, tmp: Path, *, use_cli: bool):
        audio = LocalMediaRef(path=tmp / "a.ogg", mime_type="audio/ogg")
        assets = PrePassMediaAssets(
            audio=audio, frames=[], manifest_path=tmp / "assets.json"
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
                "prepass_gemini_backend",
                "cli" if use_cli else "api",
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
        return asyncio.run(
            pp.run_pre_pass(
                client=object(),
                video_description=None,
                srt_text="1\n00:00:00,000 --> 00:00:02,000\nhello\n",
                video_path=tmp / "v.mp4",
                audio_path=tmp / "a.ogg",
                chunks=[chunk],
                pre_pass_path=tmp / "pre_pass.json",
                pre_pass_cache_dir=tmp / "cache",
            )
        )

    def test_cli_dispatch_writes_manifest_backend_cli(self):
        tmp = self._temp_dir()
        self._common_patches(tmp, use_cli=True)
        with patch.object(
            pp,
            "run_gemini_cli",
            return_value=GeminiCliResult(
                response=_VALID_PREPASS_JSON,
                requests=4,
                stats={},
                raw_envelope={},
            ),
        ) as mock_cli:
            result, cost = self._run(tmp)

        self.assertEqual(cost, 0.0)
        self.assertEqual(result.summary, "s")
        mock_cli.assert_called_once()
        # run_gemini_cli is given the schema so it owns enforcement.
        self.assertIs(mock_cli.call_args.kwargs["schema"], pp.PrePassResult)
        manifest = json.loads(
            (tmp / "cache" / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["backend"], "cli")
        self.assertTrue((tmp / "pre_pass.json").exists())

    def test_cli_quota_error_becomes_prepass_error(self):
        tmp = self._temp_dir()
        self._common_patches(tmp, use_cli=True)
        with patch.object(
            pp,
            "run_gemini_cli",
            side_effect=GeminiCliQuotaError("quota will reset after 8h"),
        ) as mock_cli:
            with self.assertRaises(PrePassError) as ctx:
                self._run(tmp)

        self.assertEqual(mock_cli.call_count, 1)
        self.assertIn("quota", str(ctx.exception).lower())
        self.assertEqual(ctx.exception.accumulated_cost, 0.0)

    def test_api_dispatch_single_call_no_retry(self):
        tmp = self._temp_dir()
        self._common_patches(tmp, use_cli=False)
        api = AsyncMock(return_value=(_prepass(), 0.12, 1))
        with patch.object(pp, "_infer_via_api", new=api):
            result, cost = self._run(tmp)

        self.assertEqual(cost, 0.12)
        self.assertEqual(result.summary, "s")
        api.assert_awaited_once()
        manifest = json.loads(
            (tmp / "cache" / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["backend"], "api")

    def test_api_failure_raises_prepass_error_without_retry(self):
        tmp = self._temp_dir()
        self._common_patches(tmp, use_cli=False)
        api = AsyncMock(side_effect=RuntimeError("genai boom"))
        with patch.object(pp, "_infer_via_api", new=api):
            with self.assertRaises(PrePassError):
                self._run(tmp)
        self.assertEqual(api.await_count, 1)


if __name__ == "__main__":
    unittest.main()
