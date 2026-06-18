import unittest

from settings import ModelSpec, Settings


class ModelSpecParseTests(unittest.TestCase):
    """The agent_*_model fields accept "model" or "model/effort" shorthand."""

    def test_model_and_effort(self):
        s = Settings(agent_chunk_model="gpt-5.5/low")
        self.assertEqual(s.agent_chunk_model.model, "gpt-5.5")
        self.assertEqual(s.agent_chunk_model.reasoning_effort, "low")

    def test_bare_model_defaults_to_high(self):
        s = Settings(agent_chunk_model="gpt-5.5")
        self.assertEqual(s.agent_chunk_model.model, "gpt-5.5")
        self.assertEqual(s.agent_chunk_model.reasoning_effort, "high")

    def test_trailing_slash_defaults_to_high(self):
        s = Settings(agent_chunk_model="gpt-5.5/")
        self.assertEqual(s.agent_chunk_model.model, "gpt-5.5")
        self.assertEqual(s.agent_chunk_model.reasoning_effort, "high")

    def test_whitespace_is_trimmed(self):
        s = Settings(agent_postprocess_model="  gpt-5.5 / medium  ")
        self.assertEqual(s.agent_postprocess_model.model, "gpt-5.5")
        self.assertEqual(s.agent_postprocess_model.reasoning_effort, "medium")

    def test_str_roundtrip(self):
        self.assertEqual(
            str(ModelSpec(model="m", reasoning_effort="low")), "m/low"
        )


if __name__ == "__main__":
    unittest.main()
