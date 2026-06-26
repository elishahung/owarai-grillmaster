import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("AGENT_GEMINI_API_KEY", "test-key")

from services.inference import gemini_agy as agy_mod
from services.inference.gemini_agy import (
    GeminiAgyError,
    GeminiAgyNotInstalledError,
    GeminiAgyQuotaError,
    clean_terminal_output,
    resolve_agy_model,
    run_gemini_agy,
    slice_marked_answer,
)


class ResolveAgyModelTests(unittest.TestCase):
    def test_maps_id_and_effort_to_display_name(self):
        self.assertEqual(
            resolve_agy_model("gemini-3.5-flash", "high"),
            "Gemini 3.5 Flash (High)",
        )
        self.assertEqual(
            resolve_agy_model("gemini-3.1-pro", "low"),
            "Gemini 3.1 Pro (Low)",
        )

    def test_case_insensitive_inputs(self):
        self.assertEqual(
            resolve_agy_model("Gemini-3.5-Flash", "HIGH"),
            "Gemini 3.5 Flash (High)",
        )

    def test_unknown_model_raises(self):
        with self.assertRaises(GeminiAgyError):
            resolve_agy_model("gpt-5.5", "high")

    def test_effort_not_offered_for_model_raises(self):
        # agy exposes only Low/High for Gemini 3.1 Pro, not Medium.
        with self.assertRaises(GeminiAgyError):
            resolve_agy_model("gemini-3.1-pro", "medium")


class CleanTerminalOutputTests(unittest.TestCase):
    def test_strips_ansi_and_carriage_returns_and_spinner(self):
        raw = (
            "\x1b[2J\x1b[1;1Hloading ⠋\rHello\x1b[0m World\r\n"
            "final line ╭──╮"
        )
        cleaned = clean_terminal_output(raw)
        self.assertIn("Hello World", cleaned)
        # The pre-\r repaint ("loading") is dropped.
        self.assertNotIn("loading", cleaned)
        self.assertIn("final line", cleaned)
        # Box-drawing glyphs are gone.
        for glyph in "╭╮╰╯│─":
            self.assertNotIn(glyph, cleaned)
        # No raw escape bytes survive.
        self.assertNotIn("\x1b", cleaned)


class SliceMarkedAnswerTests(unittest.TestCase):
    def test_extracts_between_markers(self):
        text = 'noise\n<<<AGY_BEGIN>>>\n{"a": 1}\n<<<AGY_END>>>\ntrailer'
        self.assertEqual(slice_marked_answer(text), '{"a": 1}')

    def test_falls_back_to_whole_text_without_markers(self):
        self.assertEqual(slice_marked_answer("  plain answer  "), "plain answer")


class RunGeminiAgyTests(unittest.TestCase):
    def _temp_dir(self) -> Path:
        base = Path(__file__).resolve().parents[1] / "tmp_test_artifacts"
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"tmp_agy_{uuid.uuid4().hex[:8]}"
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def _patch_which(self):
        p = patch.object(agy_mod.shutil, "which", return_value="agy")
        self.addCleanup(p.stop)
        p.start()

    def test_not_installed(self):
        with patch.object(agy_mod.shutil, "which", return_value=None):
            with self.assertRaises(GeminiAgyNotInstalledError):
                run_gemini_agy("hi", model="gemini-3.5-flash")

    def test_success_builds_argv_and_stages_prompt(self):
        self._patch_which()
        captured = {}

        def fake_pty(argv, *, cwd, env, timeout):
            captured["argv"] = argv
            captured["cwd"] = cwd
            captured["env"] = env
            # The staged prompt is readable while the workspace lives.
            captured["prompt_file_text"] = (
                Path(cwd) / agy_mod._PROMPT_FILE_NAME
            ).read_text(encoding="utf-8")
            return "chrome\n<<<AGY_BEGIN>>>\nresult body\n<<<AGY_END>>>\n"

        with patch.object(agy_mod, "_run_under_pty", side_effect=fake_pty):
            out = run_gemini_agy(
                "the full prompt",
                model="gemini-3.5-flash",
                reasoning_effort="high",
            )

        self.assertEqual(out.response, "result body")
        self.assertEqual(out.requests, 1)
        argv = captured["argv"]
        self.assertEqual(argv[0], "agy")
        self.assertIn("--print", argv)
        self.assertIn("--model", argv)
        # The id form is mapped to agy's exact display string.
        self.assertIn("Gemini 3.5 Flash (High)", argv)
        self.assertNotIn("gemini-3.5-flash", argv)
        self.assertIn("--dangerously-skip-permissions", argv)
        # Workspace + repo root are added as accessible roots.
        add_dir_count = sum(1 for a in argv if a == "--add-dir")
        self.assertGreaterEqual(add_dir_count, 2)
        # The real prompt was staged to the file, not passed on argv.
        self.assertEqual(captured["prompt_file_text"], "the full prompt")
        self.assertNotIn("the full prompt", argv)
        # The bootstrap references INPUT.md via agy's native @<path> token.
        bootstrap = argv[2]
        self.assertIn(f"@{Path(captured['cwd']) / agy_mod._PROMPT_FILE_NAME}", bootstrap)

    def test_images_attached_as_at_tokens(self):
        self._patch_which()
        work = self._temp_dir()
        frame = work / "frame.jpg"
        frame.write_bytes(b"img-bytes")
        captured = {}

        def fake_pty(argv, *, cwd, env, timeout):
            captured["argv"] = argv
            captured["cwd"] = cwd
            # The staged image lives in the workspace and is @-referenced.
            captured["staged"] = sorted(p.name for p in Path(cwd).iterdir())
            return "<<<AGY_BEGIN>>>ok<<<AGY_END>>>"

        with patch.object(agy_mod, "_run_under_pty", side_effect=fake_pty):
            run_gemini_agy("p", model="gemini-3.5-flash", images=[frame])

        bootstrap = captured["argv"][2]
        # Image staged as NN_<name> and attached via @<abs path>, not "open this".
        self.assertIn("00_frame.jpg", captured["staged"])
        self.assertIn(f"@{Path(captured['cwd']) / '00_frame.jpg'}", bootstrap)
        self.assertNotIn("open and look", bootstrap.lower())

    def test_scrubs_paid_api_keys_from_env(self):
        self._patch_which()
        captured = {}

        def fake_pty(argv, *, cwd, env, timeout):
            captured["env"] = env
            return "<<<AGY_BEGIN>>>ok<<<AGY_END>>>"

        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "paid"}, clear=False),
            patch.object(agy_mod, "_run_under_pty", side_effect=fake_pty),
        ):
            run_gemini_agy("p", model="gemini-3.5-flash")
        self.assertNotIn("GEMINI_API_KEY", captured["env"])

    def test_image_size_guard(self):
        self._patch_which()
        work = self._temp_dir()
        big = work / "frame.jpg"
        big.write_bytes(b"x" * 16)
        with patch.object(agy_mod, "_MAX_MEDIA_FILE_MB", 0):
            with self.assertRaises(GeminiAgyError):
                run_gemini_agy("p", model="gemini-3.5-flash", images=[big])

    def test_missing_image_raises(self):
        self._patch_which()
        with self.assertRaises(GeminiAgyError):
            run_gemini_agy("p", model="gemini-3.5-flash", images=[Path("does_not_exist.jpg")])

    def test_quota_text_classified(self):
        self._patch_which()

        def fake_pty(argv, *, cwd, env, timeout):
            return "Error: RESOURCE_EXHAUSTED quota exceeded (429)"

        with patch.object(agy_mod, "_run_under_pty", side_effect=fake_pty):
            with self.assertRaises(GeminiAgyQuotaError):
                run_gemini_agy("p", model="gemini-3.5-flash")

    def test_empty_output_raises(self):
        self._patch_which()

        def fake_pty(argv, *, cwd, env, timeout):
            return "\x1b[0m\r\n   \r\n"

        with patch.object(agy_mod, "_run_under_pty", side_effect=fake_pty):
            with self.assertRaises(GeminiAgyError):
                run_gemini_agy("p", model="gemini-3.5-flash")


if __name__ == "__main__":
    unittest.main()
