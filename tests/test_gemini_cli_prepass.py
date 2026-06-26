import asyncio
import json
import os
import shutil
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("AGENT_GEMINI_API_KEY", "test-key")

from services.inference import gemini_cli as cli_mod
from services.translate.pre_pass import pre_pass as pp
from services.translate.assets import LocalMediaRef, PrePassMediaAssets
from services.inference.gemini_cli import (
    GeminiCliError,
    GeminiCliNotInstalledError,
    GeminiCliQuotaError,
    GeminiCliResult,
    extract_request_count,
    run_gemini_cli,
)
from services.inference.tools import FRAME_TOOL_SCRIPTS, FrameToolStage
from services.inference.schema_enforce import extract_json_object
from services.translate.errors import PrePassError
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
        argv = mock_run.call_args.args[0]
        self.assertIn("--approval-mode", argv)
        self.assertIn("auto_edit", argv)
        self.assertIn("--policy", argv)
        self.assertNotIn("--yolo", argv)

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
        self.assertIn("--include-directories", mock_run.call_args.args[0])

    def test_cwd_is_included_for_project_file_access(self):
        self._patch_which()
        project_dir = self._temp_dir()
        mock_run = self._patch_run(return_value=_completed("ok"))
        run_gemini_cli("hi", model="m", cwd=project_dir)
        argv = mock_run.call_args.args[0]
        self.assertIn(str(project_dir.resolve()), argv)
        self.assertEqual(
            mock_run.call_args.kwargs["cwd"], str(project_dir.resolve())
        )

    def test_policy_allows_glossary_check_frame_tool(self):
        self._patch_which()
        captured: dict[str, str] = {}

        def _fake_run(cmd, **kwargs):
            policy_path = Path(cmd[cmd.index("--policy") + 1])
            captured["policy"] = policy_path.read_text(encoding="utf-8")
            return _completed("ok")

        self._patch_run(side_effect=_fake_run)
        run_gemini_cli("hi", model="m")

        self.assertIn("get_frames_for_glossary_check.py", captured["policy"])
        self.assertIn("commandPrefix", captured["policy"])
        self.assertTrue(
            FRAME_TOOL_SCRIPTS[FrameToolStage.GLOSSARY_CHECK].exists()
        )

    # NOTE: schema enforcement is no longer a gemini-cli concern — run_gemini_cli
    # is a single-shot text generator. The validate-and-repair loop is exercised
    # uniformly for all prompt-based backends in tests/test_inference.py.


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
            patch.object(pp.settings, "agent_prepass_backend", backend),
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
            video_description=None,
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

    def test_cli_dispatch_writes_manifest(self):
        tmp = self._temp_dir()
        self._common_patches(tmp, backend="gemini-cli")
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
        self.assertEqual(manifest["backend"], "gemini-cli")

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
        self._common_patches(tmp, backend="gemini-cli")
        with patch.object(
            pp,
            "run_inference",
            side_effect=GeminiCliQuotaError("quota will reset after 8h"),
        ) as mock_inf:
            with self.assertRaises(PrePassError) as ctx:
                self._run(tmp)

        self.assertEqual(mock_inf.call_count, 1)
        self.assertIn("quota", str(ctx.exception).lower())
        self.assertEqual(ctx.exception.accumulated_cost, 0.0)

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
