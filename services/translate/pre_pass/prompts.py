"""Pre-pass system-instruction assembly from prompt `.md` files.

The base instruction and conditional blocks live as `.md` under `prompts/`. The
audio-bearing phrasing in the base instruction is swapped out when the selected
backend cannot ingest audio (the agent backends), so the model is never told it
has an audio track it did not receive. With ``has_audio=True`` the base
instruction is byte-identical to the historical constant, so existing gemini
caches do not invalidate (guarded by a hash-stability test).
"""

from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).parent / "prompts"


def _load(name: str) -> str:
    return (_DIR / name).read_text(encoding="utf-8")


pre_pass_instruction = _load("pre_pass.md")
OFFICIAL_SOURCE_METADATA_INSTRUCTION = _load("official_source_metadata.md")
FIXED_GLOSSARY_INSTRUCTION = _load("fixed_glossary.md")
FIXED_GLOSSARY_FULL_INSTRUCTION = _load("fixed_glossary_full.md")
PARENT_PRE_PASS_INSTRUCTION = _load("parent_pre_pass.md")


# (find, replace) pairs applied to the base instruction when no audio is
# available. Each `find` MUST occur verbatim in pre_pass.md — a unit test
# asserts this so silent drift is caught. Only the base instruction is
# conditioned; the conditional blocks mention audio only as one option among
# image/SRT/ASR evidence, which is harmless when audio is simply absent.
_NO_AUDIO_SUBS: list[tuple[str, str]] = [
    (
        "along with the **Full Source Audio**, the supplied **Reference "
        "Images**, and program title/description. Treat the images as the "
        "truth source for visible facts, the audio as the truth source for "
        "spoken content and tone, and the ASR SRT as the timing/text scaffold "
        "to audit.",
        "along with the supplied **Reference Images** and program "
        "title/description. Treat the images as the truth source for visible "
        "facts and the ASR SRT as the (fallible) transcript and timing/text "
        "scaffold to audit. No audio track is available for this run.",
    ),
    (
        "3. **Full Source Audio** — The original audio track. Crucial for "
        "understanding the true context, tone, and identifying ASR errors.\n",
        "3. **Source Audio** — NOT available for this run; rely on the SRT "
        "text, reference images, and program description instead.\n",
    ),
    (
        "comedic style based on the audio vibe.",
        "comedic style based on the SRT dialogue and reference images.",
    ),
    (
        "ASR corrections (CRITICAL: Verify via Audio. If the source text has "
        "misrecognized text but you hear the correct term in the audio, map "
        "the incorrect text to the correct translation.",
        "ASR corrections (CRITICAL: cross-check against the reference images, "
        "program description, and surrounding context. If the source text is "
        "misrecognized but the correct term is recoverable from that evidence, "
        "map the incorrect text to the correct translation.",
    ),
    (
        "Scan the full SRT, listen to the audio, inspect the images, and "
        "check the program description thoroughly",
        "Scan the full SRT, inspect the images, and check the program "
        "description thoroughly",
    ),
    (
        "**tone_notes**: ~100 chars on register/energy derived directly from "
        "listening to the audio.",
        "**tone_notes**: ~100 chars on register/energy inferred from the SRT "
        "dialogue, reference images, and program description.",
    ),
    (
        "inserted captions, and scene/location changes when they conflict "
        "with audio impressions or ASR text.",
        "inserted captions, and scene/location changes when they conflict "
        "with the ASR text.",
    ),
]


def build_pre_pass_instruction(*, has_audio: bool) -> str:
    """Return the base pre-pass instruction, audio-conditioned.

    ``has_audio=True`` returns the base instruction unchanged (byte-identical
    to the historical constant). ``has_audio=False`` swaps audio-bearing
    phrasing for image/SRT-only phrasing.
    """
    text = pre_pass_instruction
    if not has_audio:
        for find, repl in _NO_AUDIO_SUBS:
            text = text.replace(find, repl)
    return text
