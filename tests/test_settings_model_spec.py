import unittest

from settings import ModelSpec, Settings


class ModelSpecParseTests(unittest.TestCase):
    """The agent_*_model fields accept "backend/model[/effort]" shorthand."""

    def test_backend_model_and_effort(self):
        s = Settings(agent_chunk_model="codex/gpt-5.5/low")
        self.assertEqual(s.agent_chunk_model.backend, "codex")
        self.assertEqual(s.agent_chunk_model.model, "gpt-5.5")
        self.assertEqual(s.agent_chunk_model.reasoning_effort, "low")

    def test_missing_effort_defaults_to_high(self):
        s = Settings(agent_chunk_model="gemini-api/gemini-3-flash-preview")
        self.assertEqual(s.agent_chunk_model.backend, "gemini-api")
        self.assertEqual(s.agent_chunk_model.model, "gemini-3-flash-preview")
        self.assertEqual(s.agent_chunk_model.reasoning_effort, "high")

    def test_trailing_slash_defaults_to_high(self):
        s = Settings(agent_chunk_model="codex/gpt-5.5/")
        self.assertEqual(s.agent_chunk_model.backend, "codex")
        self.assertEqual(s.agent_chunk_model.model, "gpt-5.5")
        self.assertEqual(s.agent_chunk_model.reasoning_effort, "high")

    def test_whitespace_is_trimmed(self):
        s = Settings(agent_postprocess_model="  codex / gpt-5.5 / medium  ")
        self.assertEqual(s.agent_postprocess_model.backend, "codex")
        self.assertEqual(s.agent_postprocess_model.model, "gpt-5.5")
        self.assertEqual(s.agent_postprocess_model.reasoning_effort, "medium")

    def test_extra_effort_is_supported_and_normalized(self):
        s = Settings(agent_chunk_model="codex/gpt-5.5/EXTRA")
        self.assertEqual(s.agent_chunk_model.model, "gpt-5.5")
        self.assertEqual(s.agent_chunk_model.reasoning_effort, "extra")

    def test_unknown_effort_raises(self):
        with self.assertRaises(ValueError):
            Settings(agent_chunk_model="codex/gpt-5.5/xhigh")

    def test_bare_model_without_backend_raises(self):
        with self.assertRaises(ValueError):
            Settings(agent_chunk_model="gpt-5.5")

    def test_too_many_segments_raises(self):
        with self.assertRaises(ValueError):
            Settings(agent_chunk_model="codex/gpt-5.5/medium/high")

    def test_common_model_default(self):
        # _env_file=None keeps the developer's .env from shadowing the default.
        s = Settings(_env_file=None)
        self.assertEqual(s.agent_common_model.backend, "codex")
        self.assertEqual(s.agent_common_model.model, "gpt-5.5")
        self.assertEqual(s.agent_common_model.reasoning_effort, "medium")

    def test_str_roundtrip(self):
        self.assertEqual(
            str(ModelSpec(backend="codex", model="m", reasoning_effort="low")),
            "codex/m/low",
        )


if __name__ == "__main__":
    unittest.main()
