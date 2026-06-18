import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import BaseModel

import services.inference as inf
from services.inference import (
    Backend,
    InferenceError,
    InferenceResult,
    UnsupportedMediaError,
    backend_supports_audio,
    is_agent_backend,
    is_gemini_backend,
    run_inference,
)
from services.inference.base import truncate_middle
from services.inference.gemini_cli import GeminiCliResult
from services.inference.schema_enforce import (
    SchemaValidationError,
    enforce_schema,
    extract_json_object,
    schema_instruction,
)


class _Demo(BaseModel):
    a: int


class CapabilityTests(unittest.TestCase):
    def test_audio_capability(self):
        self.assertTrue(backend_supports_audio(Backend.GEMINI_API))
        self.assertTrue(backend_supports_audio(Backend.GEMINI_CLI))
        self.assertFalse(backend_supports_audio(Backend.CODEX))
        self.assertFalse(backend_supports_audio(Backend.CLAUDE))

    def test_family_helpers(self):
        self.assertTrue(is_gemini_backend(Backend.GEMINI_CLI))
        self.assertFalse(is_gemini_backend(Backend.CODEX))

    def test_agent_is_everything_except_gemini_api(self):
        # api-vs-agent is the only taxonomy: gemini-cli is an agent too.
        self.assertTrue(is_agent_backend(Backend.GEMINI_CLI))
        self.assertTrue(is_agent_backend(Backend.CODEX))
        self.assertTrue(is_agent_backend(Backend.CLAUDE))
        self.assertFalse(is_agent_backend(Backend.GEMINI_API))

    def test_agent_backend_alias_values(self):
        # Post-processing selects via the legacy alias with "codex"/"claude".
        self.assertEqual(inf.AgentBackend("codex"), Backend.CODEX)
        self.assertEqual(inf.AgentBackend("claude"), Backend.CLAUDE)


class TruncateMiddleTests(unittest.TestCase):
    def test_short_text_passes_through(self):
        self.assertEqual(truncate_middle("abc"), "abc")

    def test_boundary_equal_to_head_plus_tail_passes_through(self):
        text = "x" * 100  # head(50) + tail(50)
        self.assertEqual(truncate_middle(text), text)

    def test_long_text_keeps_head_and_tail_and_counts_omission(self):
        text = "H" * 50 + "M" * 30 + "T" * 50  # 130 chars, 30 in the middle
        out = truncate_middle(text)
        self.assertTrue(out.startswith("H" * 50))
        self.assertTrue(out.endswith("T" * 50))
        self.assertIn("[30 chars omitted]", out)
        self.assertNotIn("M", out)

    def test_trailing_whitespace_stripped_before_measuring(self):
        self.assertEqual(truncate_middle("abc\n\n  "), "abc")


class EnforceSchemaTests(unittest.TestCase):
    def test_success_first_try(self):
        calls = []

        def invoke_once(prompt):
            calls.append(prompt)
            return '{"a": 1}', 1

        text, requests = enforce_schema(
            invoke_once, schema=_Demo, base_prompt="P", max_retries=3
        )
        self.assertEqual(text, '{"a": 1}')
        self.assertEqual(requests, 1)
        self.assertEqual(calls, ["P"])

    def test_repair_then_succeeds(self):
        outputs = iter(['{"a": "bad"}', '{"a": 7}'])
        prompts = []

        def invoke_once(prompt):
            prompts.append(prompt)
            return next(outputs), 1

        text, requests = enforce_schema(
            invoke_once, schema=_Demo, base_prompt="P", max_retries=3
        )
        self.assertEqual(text, '{"a": 7}')
        self.assertEqual(requests, 2)
        self.assertNotIn("修正要求", prompts[0])
        self.assertIn("修正要求", prompts[1])

    def test_exhaustion_raises(self):
        def invoke_once(prompt):
            return '{"a": "bad"}', 1

        with self.assertRaises(SchemaValidationError):
            enforce_schema(
                invoke_once, schema=_Demo, base_prompt="P", max_retries=3
            )

    def test_schema_instruction_mentions_json_schema(self):
        self.assertIn("JSON Schema", schema_instruction(_Demo))

    def test_extract_json_object_unwraps_fence(self):
        self.assertEqual(
            extract_json_object('```json\n{"a": 1}\n```'), '{"a": 1}'
        )


class RunInferenceDispatchTests(unittest.TestCase):
    def test_audio_rejected_for_agent_backend(self):
        with self.assertRaises(UnsupportedMediaError):
            run_inference(
                backend=Backend.CODEX,
                prompt="hi",
                audio=[Path("a.ogg")],
            )

    def test_gemini_requires_model(self):
        with self.assertRaises(InferenceError):
            run_inference(backend=Backend.GEMINI_API, prompt="hi")

    def test_agent_no_schema_returns_raw_message(self):
        with patch.object(inf, "run_codex_exec", return_value="done") as m:
            result = run_inference(
                backend=Backend.CODEX, prompt="hi", system_prompt="SYS"
            )
        self.assertEqual(result.text, "done")
        self.assertEqual(result.cost, 0.0)
        # system_prompt is prepended to the user prompt for agent backends.
        self.assertEqual(m.call_args.kwargs["prompt"], "SYS\n\nhi")

    def test_agent_schema_mode_validates_and_repairs(self):
        outputs = iter(['{"a": "bad"}', '{"a": 5}'])
        with patch.object(
            inf,
            "run_claude_sdk_exec",
            side_effect=lambda **kw: next(outputs),
        ) as m:
            result = run_inference(
                backend=Backend.CLAUDE, prompt="hi", schema=_Demo
            )
        self.assertEqual(result.text, '{"a": 5}')
        self.assertEqual(result.requests, 2)
        self.assertEqual(m.call_count, 2)
        # The JSON Schema instruction rides along on the first attempt.
        self.assertIn("JSON Schema", m.call_args_list[0].kwargs["prompt"])

    def test_gemini_api_routes_to_backend(self):
        sentinel = InferenceResult(text='{"a": 1}', cost=0.12, requests=1)
        with patch.object(
            inf, "run_gemini_api", return_value=sentinel
        ) as m:
            result = run_inference(
                backend=Backend.GEMINI_API,
                prompt="hi",
                system_prompt="SYS",
                schema=_Demo,
                model="gemini-3.1-pro-preview",
            )
        self.assertIs(result, sentinel)
        self.assertEqual(m.call_args.kwargs["model"], "gemini-3.1-pro-preview")
        self.assertEqual(m.call_args.kwargs["system_prompt"], "SYS")
        self.assertIs(m.call_args.kwargs["schema"], _Demo)

    def test_gemini_cli_routes_media_and_concats_prompt(self):
        cli_result = GeminiCliResult(
            response="raw srt", requests=3, stats={}, raw_envelope={}
        )
        with patch.object(
            inf, "run_gemini_cli", return_value=cli_result
        ) as m:
            result = run_inference(
                backend=Backend.GEMINI_CLI,
                prompt="user",
                system_prompt="SYS",
                images=[Path("f.jpg")],
                audio=[Path("a.ogg")],
                model="gemini-3-flash-preview",
            )
        self.assertEqual(result.text, "raw srt")
        self.assertEqual(result.requests, 3)
        # Single concatenated prompt; media = audio first, then images.
        self.assertEqual(m.call_args.args[0], "SYS\n\nuser")
        self.assertEqual(
            m.call_args.kwargs["media_files"], [Path("a.ogg"), Path("f.jpg")]
        )

    def test_model_and_effort_thread_through_to_agent_runner(self):
        with patch.object(inf, "run_codex_exec", return_value="ok") as m:
            run_inference(
                backend=Backend.CODEX,
                prompt="hi",
                model="gpt-5.5",
                reasoning_effort="low",
            )
        self.assertEqual(m.call_args.kwargs["model"], "gpt-5.5")
        self.assertEqual(m.call_args.kwargs["reasoning_effort"], "low")

    def test_agent_schema_retries_use_shared_hardcoded_cap(self):
        # The repair cap is the single hardcoded MAX_SCHEMA_RETRIES constant,
        # shared by every prompt-based backend (no per-call / settings knob).
        from services.inference.schema_enforce import MAX_SCHEMA_RETRIES

        with patch.object(
            inf, "run_claude_sdk_exec", return_value='{"a": "bad"}'
        ) as m:
            with self.assertRaises(SchemaValidationError):
                run_inference(
                    backend=Backend.CLAUDE, prompt="hi", schema=_Demo
                )
        self.assertEqual(m.call_count, MAX_SCHEMA_RETRIES)


class CodexCommandTests(unittest.TestCase):
    """codex argv carries the passed model + the mapped reasoning effort."""

    def test_codex_argv_uses_model_and_reasoning_effort(self):
        from types import SimpleNamespace

        import services.inference.codex as codex

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return SimpleNamespace(returncode=0, stdout="done", stderr="")

        with (
            patch.object(codex.shutil, "which", return_value="codex"),
            patch.object(codex.subprocess, "run", side_effect=fake_run),
        ):
            codex.run_codex_exec(
                prompt="hi",
                cwd=Path("."),
                model="gpt-5.5",
                reasoning_effort="low",
            )
        cmd = captured["cmd"]
        self.assertIn("gpt-5.5", cmd)
        self.assertIn("model_reasoning_effort=low", cmd)

    def test_codex_falls_back_to_default_model(self):
        from types import SimpleNamespace

        import services.inference.codex as codex

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return SimpleNamespace(returncode=0, stdout="done", stderr="")

        with (
            patch.object(codex.shutil, "which", return_value="codex"),
            patch.object(codex.subprocess, "run", side_effect=fake_run),
        ):
            codex.run_codex_exec(prompt="hi", cwd=Path("."))
        self.assertIn(codex._DEFAULT_MODEL, captured["cmd"])


if __name__ == "__main__":
    unittest.main()
