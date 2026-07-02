import unittest

from services.translate.chunk import prompts as C
from services.translate.pre_pass import prompts as P


class NoAudioSubstitutionTests(unittest.TestCase):
    def test_every_pre_pass_find_present_in_base(self):
        for find, _ in P._NO_AUDIO_SUBS:
            self.assertIn(find, P.pre_pass_instruction)

    def test_every_chunk_find_present_in_base(self):
        for find, _ in C._NO_AUDIO_SUBS:
            self.assertIn(find, C.chunk_instruction)

    def test_pre_pass_no_audio_drops_hallucination_phrases(self):
        text = P.build_pre_pass_instruction(has_audio=False)
        for phrase in (
            "Full Source Audio",
            "listen to the audio",
            "Verify via Audio",
            "audio vibe",
            "listening to the audio",
        ):
            self.assertNotIn(phrase, text)

    def test_chunk_no_audio_drops_hallucination_phrases(self):
        text = C.build_chunk_instruction(has_audio=False)
        for phrase in (
            "chunk-specific audio slice",
            "chunk audio slice",
            "as heard in the audio",
            "images and audio",
        ):
            self.assertNotIn(phrase, text)

    def test_no_audio_changes_output(self):
        self.assertNotEqual(
            P.build_pre_pass_instruction(has_audio=True),
            P.build_pre_pass_instruction(has_audio=False),
        )
        self.assertNotEqual(
            C.build_chunk_instruction(has_audio=True),
            C.build_chunk_instruction(has_audio=False),
        )


class PrePassPromptScopeTests(unittest.TestCase):
    def test_base_pre_pass_prompt_does_not_expose_agent_web_search(self):
        text = P.build_pre_pass_instruction(has_audio=True)
        self.assertNotIn("Use built-in web search", text)
        self.assertNotIn("The SRT is not ground truth", text)


if __name__ == "__main__":
    unittest.main()
