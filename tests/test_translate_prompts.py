import hashlib
import unittest

from services.translate.chunk import prompts as C
from services.translate.pre_pass import prompts as P


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Captured from the original instructions.py constants. The has_audio=True
# assembly MUST stay byte-identical to these so existing gemini caches (keyed on
# an instruction digest) are not invalidated by the .md migration.
_PRE_PASS_SHA = (
    "ff45ff44f212bd09c82563518fb850ee3ab21e1b257ea4910dfbd93ea5b02bcf"
)
_CHUNK_SHA = "6db96ef3bb60583a50584bc8a33161558ca00fef596367f1287ec8aaaf4a02c4"
_BLOCK_SHAS = {
    "OFFICIAL_SOURCE_METADATA_INSTRUCTION": (
        "dc97bbcf819523d86f92b46d2f2be341970f3ee01ce9f12379316ecf611f2253"
    ),
    "FIXED_GLOSSARY_INSTRUCTION": (
        "de522377cc296a1a91aefb58ba9c797773a3ffbb3a583395b69918f8ccfc148b"
    ),
    "FIXED_GLOSSARY_FULL_INSTRUCTION": (
        "59d6928a23e0c883407f9852b82cc37d674e5fa5abc11daf6fa93cdb5dff3aeb"
    ),
    "PARENT_PRE_PASS_INSTRUCTION": (
        "8b173fc53823b0e02b9b2cce5793c79b858209c8f01d77411db6005c86390c31"
    ),
}


class PromptHashStabilityTests(unittest.TestCase):
    def test_pre_pass_has_audio_byte_identical(self):
        self.assertEqual(
            _sha(P.build_pre_pass_instruction(has_audio=True)), _PRE_PASS_SHA
        )

    def test_chunk_has_audio_byte_identical(self):
        self.assertEqual(
            _sha(C.build_chunk_instruction(has_audio=True)), _CHUNK_SHA
        )

    def test_conditional_blocks_byte_identical(self):
        for name, sha in _BLOCK_SHAS.items():
            self.assertEqual(_sha(getattr(P, name)), sha, name)


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


if __name__ == "__main__":
    unittest.main()
