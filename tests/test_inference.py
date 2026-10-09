import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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
from services.inference import base as base_mod
from services.inference.base import truncate_middle
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
        # agy (Antigravity CLI) hears audio through view_file.
        self.assertTrue(backend_supports_audio(Backend.AGY))
        self.assertFalse(backend_supports_audio(Backend.CODEX))
        self.assertFalse(backend_supports_audio(Backend.CLAUDE))

    def test_family_helpers(self):
        self.assertTrue(is_gemini_backend(Backend.GEMINI_API))
        # agy is a Gemini backend (so it requires an explicit model).
        self.assertTrue(is_gemini_backend(Backend.AGY))
        self.assertFalse(is_gemini_backend(Backend.CODEX))

    def test_agent_is_everything_except_gemini_api(self):
        # api-vs-agent is the only taxonomy: agy is an agent too.
        self.assertTrue(is_agent_backend(Backend.AGY))
        self.assertTrue(is_agent_backend(Backend.CODEX))
        self.assertTrue(is_agent_backend(Backend.CLAUDE))
        self.assertFalse(is_agent_backend(Backend.GEMINI_API))

    def test_postprocess_backend_string_values(self):
        # Post-processing selects a backend from the "codex"/"claude" strings
        # stored in the settings.agent_*_model backend segment.
        self.assertEqual(Backend("codex"), Backend.CODEX)
        self.assertEqual(Backend("claude"), Backend.CLAUDE)


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
            return InferenceResult(text='{"a": 1}', requests=1)

        result = enforce_schema(
            invoke_once, schema=_Demo, base_prompt="P", max_retries=3
        )
        self.assertEqual(result.text, '{"a": 1}')
        self.assertEqual(result.requests, 1)
        self.assertEqual(calls, ["P"])

    def test_repair_then_succeeds(self):
        outputs = iter(['{"a": "bad"}', '{"a": 7}'])
        prompts = []

        def invoke_once(prompt):
            prompts.append(prompt)
            return InferenceResult(text=next(outputs), cost=0.5, requests=1)

        result = enforce_schema(
            invoke_once, schema=_Demo, base_prompt="P", max_retries=3
        )
        self.assertEqual(result.text, '{"a": 7}')
        self.assertEqual(result.requests, 2)
        # cost and requests accumulate across attempts
        self.assertEqual(result.cost, 1.0)
        self.assertNotIn("修正要求", prompts[0])
        self.assertIn("修正要求", prompts[1])

    def test_exhaustion_raises(self):
        def invoke_once(prompt):
            return InferenceResult(text='{"a": "bad"}', requests=1)

        with self.assertRaises(SchemaValidationError):
            enforce_schema(
                invoke_once, schema=_Demo, base_prompt="P", max_retries=3
            )

    def test_validate_hook_rejects_schema_valid_output(self):
        outputs = iter(['{"a": 1}', '{"a": 7}'])
        prompts = []

        def invoke_once(prompt):
            prompts.append(prompt)
            return InferenceResult(text=next(outputs), requests=1)

        def validate(parsed):
            if parsed.a < 7:
                raise ValueError("a must be at least 7")

        result = enforce_schema(
            invoke_once,
            schema=_Demo,
            base_prompt="P",
            validate=validate,
            max_retries=3,
        )
        self.assertEqual(result.text, '{"a": 7}')
        self.assertEqual(result.requests, 2)
        # the invariant message is what the model is re-prompted with
        self.assertIn("a must be at least 7", prompts[1])

    def test_validate_hook_exhaustion_raises(self):
        def invoke_once(prompt):
            return InferenceResult(text='{"a": 1}', requests=1)

        def validate(parsed):
            raise ValueError("never good enough")

        with self.assertRaises(SchemaValidationError):
            enforce_schema(
                invoke_once,
                schema=_Demo,
                base_prompt="P",
                validate=validate,
                max_retries=2,
            )

    def test_validate_without_schema_is_rejected(self):
        with self.assertRaises(InferenceError):
            run_inference(
                backend=Backend.CODEX,
                prompt="p",
                validate=lambda parsed: None,
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
        with patch.object(inf, "run_gemini_api", return_value=sentinel) as m:
            result = run_inference(
                backend=Backend.GEMINI_API,
                prompt="hi",
                system_prompt="SYS",
                schema=_Demo,
                model="gemini-3.1-pro-preview",
            )
        # the shared repair loop returns a copy carrying summed cost/requests
        self.assertEqual(result, sentinel)
        self.assertEqual(m.call_args.kwargs["model"], "gemini-3.1-pro-preview")
        self.assertEqual(m.call_args.kwargs["system_prompt"], "SYS")
        self.assertIs(m.call_args.kwargs["schema"], _Demo)

    def test_agy_routes_images_and_audio(self):
        from services.inference.agy import AgyResult

        agy_result = AgyResult(response="agy out", requests=1)
        with patch.object(inf, "run_agy", return_value=agy_result) as m:
            result = run_inference(
                backend=Backend.AGY,
                prompt="user",
                system_prompt="SYS",
                images=[Path("f.jpg")],
                audio=[Path("a.ogg")],
                model="gemini-3.8-flash",
                reasoning_effort="low",
            )
        self.assertEqual(result.text, "agy out")
        self.assertEqual(result.cost, 0.0)
        # Single concatenated prompt; images + audio + model + effort forwarded
        # (the wrapper maps model/effort to agy's --model string).
        self.assertEqual(m.call_args.args[0], "SYS\n\nuser")
        self.assertEqual(m.call_args.kwargs["images"], [Path("f.jpg")])
        self.assertEqual(m.call_args.kwargs["audio"], [Path("a.ogg")])
        self.assertEqual(m.call_args.kwargs["model"], "gemini-3.8-flash")
        self.assertEqual(m.call_args.kwargs["reasoning_effort"], "low")

    def test_agy_requires_model(self):
        with self.assertRaises(InferenceError):
            run_inference(backend=Backend.AGY, prompt="hi")

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
                run_inference(backend=Backend.CLAUDE, prompt="hi", schema=_Demo)
        self.assertEqual(m.call_count, MAX_SCHEMA_RETRIES)


class GeminiApiReasoningTests(unittest.TestCase):
    def test_extra_clamps_to_high_thinking_level(self):
        from services.inference.gemini_api import resolve_gemini_thinking_level

        self.assertEqual(resolve_gemini_thinking_level("extra").name, "HIGH")

    def test_max_and_ultra_clamp_to_high_thinking_level(self):
        from services.inference.gemini_api import resolve_gemini_thinking_level

        self.assertEqual(resolve_gemini_thinking_level("max").name, "HIGH")
        self.assertEqual(resolve_gemini_thinking_level("ultra").name, "HIGH")


class TimeoutSettingTests(unittest.TestCase):
    """The shared per-invocation timeout comes from AGENT_TIMEOUT_MINUTES."""

    def test_default_timeout_follows_setting(self):
        from types import SimpleNamespace

        import services.inference.base as base
        import services.inference.codex as codex

        with patch.object(base, "settings", SimpleNamespace(agent_timeout_minutes=7)):
            self.assertEqual(base.default_timeout_secs(), 7 * 60)

            captured = {}

            def fake_run(cmd, **kwargs):
                captured["timeout"] = kwargs["timeout"]
                return SimpleNamespace(returncode=0, stdout="done", stderr="")

            with (
                patch.object(codex.shutil, "which", return_value="codex"),
                patch.object(codex, "run_cli", side_effect=fake_run),
            ):
                codex.run_codex_exec(prompt="hi", cwd=Path("."))
        self.assertEqual(captured["timeout"], 7 * 60)


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
            patch.object(codex, "run_cli", side_effect=fake_run),
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

    def test_codex_extra_maps_to_xhigh(self):
        from types import SimpleNamespace

        import services.inference.codex as codex

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return SimpleNamespace(returncode=0, stdout="done", stderr="")

        with (
            patch.object(codex.shutil, "which", return_value="codex"),
            patch.object(codex, "run_cli", side_effect=fake_run),
        ):
            codex.run_codex_exec(
                prompt="hi",
                cwd=Path("."),
                model="gpt-5.5",
                reasoning_effort="extra",
            )
        self.assertIn("model_reasoning_effort=xhigh", captured["cmd"])

    def test_codex_max_and_ultra_pass_through(self):
        from services.inference.codex import resolve_codex_reasoning_effort

        self.assertEqual(resolve_codex_reasoning_effort("max"), "max")
        self.assertEqual(resolve_codex_reasoning_effort("ultra"), "ultra")

    def test_codex_falls_back_to_default_model(self):
        from types import SimpleNamespace

        import services.inference.codex as codex

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return SimpleNamespace(returncode=0, stdout="done", stderr="")

        with (
            patch.object(codex.shutil, "which", return_value="codex"),
            patch.object(codex, "run_cli", side_effect=fake_run),
        ):
            codex.run_codex_exec(prompt="hi", cwd=Path("."))
        self.assertIn(codex._DEFAULT_MODEL, captured["cmd"])

    def test_codex_web_search_flag_enables_tool(self):
        from types import SimpleNamespace

        import services.inference.codex as codex

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return SimpleNamespace(returncode=0, stdout="done", stderr="")

        with (
            patch.object(codex.shutil, "which", return_value="codex"),
            patch.object(codex, "run_cli", side_effect=fake_run),
        ):
            codex.run_codex_exec(prompt="hi", cwd=Path("."), web_search=True)
        self.assertIn("tools.web_search=true", captured["cmd"])

    def test_codex_web_search_off_by_default(self):
        from types import SimpleNamespace

        import services.inference.codex as codex

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return SimpleNamespace(returncode=0, stdout="done", stderr="")

        with (
            patch.object(codex.shutil, "which", return_value="codex"),
            patch.object(codex, "run_cli", side_effect=fake_run),
        ):
            codex.run_codex_exec(prompt="hi", cwd=Path("."))
        self.assertNotIn("tools.web_search=true", captured["cmd"])


class ClaudeCommandTests(unittest.TestCase):
    def test_claude_extra_maps_to_xhigh(self):
        import claude_agent_sdk
        import services.inference.claude_sdk as claude

        captured = {}

        async def fake_query(*, prompt, options):
            captured["prompt"] = prompt
            captured["effort"] = options.effort
            if False:
                yield None

        with patch.object(claude_agent_sdk, "query", fake_query):
            result = claude.run_claude_sdk_exec(
                prompt="hi",
                cwd=Path("."),
                model="claude-opus-4-8",
                reasoning_effort="extra",
            )

        self.assertEqual(result, "")
        self.assertEqual(captured["prompt"], "hi")
        self.assertEqual(captured["effort"], "xhigh")

    def test_claude_max_maps_to_max_and_ultra_clamps_to_max(self):
        from services.inference.claude_sdk import resolve_claude_reasoning_effort

        self.assertEqual(resolve_claude_reasoning_effort("max"), "max")
        self.assertEqual(resolve_claude_reasoning_effort("ultra"), "max")

    def test_claude_surfaces_authentication_error_result(self):
        import claude_agent_sdk
        import services.inference.claude_sdk as claude

        async def fake_query(*, prompt, options):
            yield claude_agent_sdk.ResultMessage(
                subtype="success",
                duration_ms=100,
                duration_api_ms=50,
                is_error=True,
                num_turns=1,
                session_id="session",
                result=(
                    "Failed to authenticate. API Error: 401 OAuth access "
                    "token has expired."
                ),
                api_error_status=401,
            )
            raise Exception("Claude Code returned an error result: success")

        with patch.object(claude_agent_sdk, "query", fake_query):
            with self.assertRaisesRegex(
                claude.ClaudeSDKExecError,
                r"Claude authentication failed \(HTTP 401\).*"
                r"claude auth login --claudeai",
            ):
                claude.run_claude_sdk_exec(prompt="hi", cwd=Path("."))

    def test_claude_surfaces_other_api_error_result(self):
        import claude_agent_sdk
        import services.inference.claude_sdk as claude

        async def fake_query(*, prompt, options):
            yield claude_agent_sdk.ResultMessage(
                subtype="success",
                duration_ms=100,
                duration_api_ms=50,
                is_error=True,
                num_turns=1,
                session_id="session",
                result="The selected model is not available.",
                api_error_status=403,
            )
            raise Exception("Claude Code returned an error result: success")

        with patch.object(claude_agent_sdk, "query", fake_query):
            with self.assertRaisesRegex(
                claude.ClaudeSDKExecError,
                r"Claude API request failed \(HTTP 403\): "
                r"The selected model is not available\.",
            ):
                claude.run_claude_sdk_exec(prompt="hi", cwd=Path("."))

    def test_claude_keeps_rate_limit_error_specialized(self):
        import claude_agent_sdk
        import services.inference.claude_sdk as claude

        async def fake_query(*, prompt, options):
            yield claude_agent_sdk.ResultMessage(
                subtype="success",
                duration_ms=100,
                duration_api_ms=50,
                is_error=True,
                num_turns=1,
                session_id="session",
                result="Session limit reached; resets at midnight.",
                api_error_status=429,
            )
            raise Exception("Claude Code returned an error result: success")

        with patch.object(claude_agent_sdk, "query", fake_query):
            with self.assertRaisesRegex(
                claude.ClaudeSDKRateLimitError,
                "Session limit reached",
            ):
                claude.run_claude_sdk_exec(prompt="hi", cwd=Path("."))


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


class RunCliTreeKillTests(unittest.TestCase):
    """The timeout path must tree-kill before draining pipes (see base.run_cli)."""

    def test_timeout_tree_kills_then_drains_and_reraises(self):
        proc = MagicMock()
        proc.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="codex", timeout=1),
            ("", ""),
        ]
        with (
            patch.object(base_mod.subprocess, "Popen", return_value=proc),
            patch.object(base_mod, "kill_process_tree") as kill,
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                base_mod.run_cli(["codex"], input="hi", timeout=1)
        kill.assert_called_once_with(proc)
        # The drain after the kill must itself be time-bounded.
        self.assertEqual(proc.communicate.call_count, 2)
        self.assertEqual(
            proc.communicate.call_args.kwargs["timeout"],
            base_mod._POST_KILL_DRAIN_SECS,
        )

    def test_success_returns_completed_process(self):
        proc = MagicMock()
        proc.communicate.return_value = ("out", "err")
        proc.returncode = 0
        with patch.object(base_mod.subprocess, "Popen", return_value=proc):
            result = base_mod.run_cli(["codex"], input="hi", timeout=1)
        self.assertEqual(result.stdout, "out")
        self.assertEqual(result.stderr, "err")
        self.assertEqual(result.returncode, 0)
        proc.communicate.assert_called_once_with("hi", timeout=1)


if __name__ == "__main__":
    unittest.main()
