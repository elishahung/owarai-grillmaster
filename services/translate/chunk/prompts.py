"""Chunk-worker system-instruction assembly from the prompt `.md` file.

The base instruction lives as ``prompts/chunk.md``. Audio-bearing phrasing is
swapped out when the backend cannot ingest audio (agent backends). With
``has_audio=True`` the instruction is byte-identical to the historical constant
so existing gemini caches do not invalidate (guarded by a hash-stability test).
"""

from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).parent / "prompts"


def _load(name: str) -> str:
    return (_DIR / name).read_text(encoding="utf-8")


chunk_instruction = _load("chunk.md")


# (find, replace) pairs applied when no audio is available. Each `find` MUST
# occur verbatim in chunk.md (asserted by a unit test).
_NO_AUDIO_SUBS: list[tuple[str, str]] = [
    (
        "the **chunk-specific audio slice**, and several **reference images "
        "sampled from the same chunk range**. You translate ONLY the blocks "
        "in your assigned index range, and you must focus your listening and "
        "visual inspection strictly on that range.",
        "and several **reference images sampled from the same chunk range** "
        "(no audio is available for this run). You translate ONLY the blocks "
        "in your assigned index range, and you must focus your visual "
        "inspection strictly on that range.",
    ),
    (
        "Treat the **chunk images** as the truth source for visible facts "
        "(who is on screen, reactions, props, captions, costumes, locations, "
        "scene changes), the **chunk audio slice** as the truth source for "
        "spoken content, tone, rhythm, and emotion, and the ASR SRT as the "
        "block/timecode scaffold plus a fallible transcript. When they "
        "conflict, prefer images for visual context, audio for what was said, "
        "and use ASR mainly to preserve segmentation and guide translation.",
        "Treat the **chunk images** as the truth source for visible facts "
        "(who is on screen, reactions, props, captions, costumes, locations, "
        "scene changes) and the ASR SRT as the block/timecode scaffold and "
        "the (fallible) transcript of what was said. When they conflict, "
        "prefer images for visual context and use the ASR text for spoken "
        "content and to preserve segmentation. No audio track is available "
        "for this run.",
    ),
    (
        "**Correct ASR, then localize naturally:** Use the images and audio "
        "to correct weird ASR mistakes, resolve homophone mix-ups, identify "
        "speakers, and understand nonsensical raw text.",
        "**Correct ASR, then localize naturally:** Use the images, the "
        "pre-pass briefing, and surrounding context to correct weird ASR "
        "mistakes, resolve homophone mix-ups, identify speakers, and "
        "understand nonsensical raw text.",
    ),
    (
        "only where they genuinely match the speaker's rhythm/emotion as "
        "heard in the audio.",
        "only where they genuinely match the speaker's rhythm/emotion "
        "inferred from the dialogue and context.",
    ),
    (
        "unless the subject is unambiguously recoverable from the audio, "
        "source line, `segment_summary`, or immediately preceding blocks.",
        "unless the subject is unambiguously recoverable from the source "
        "line, `segment_summary`, or immediately preceding blocks.",
    ),
]


def build_chunk_instruction(*, has_audio: bool) -> str:
    """Return the chunk instruction, audio-conditioned (see module docstring)."""
    text = chunk_instruction
    if not has_audio:
        for find, repl in _NO_AUDIO_SUBS:
            text = text.replace(find, repl)
    return text
