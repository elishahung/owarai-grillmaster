"""Subtitle translation pipeline.

A pre-pass stage scans the full SRT once to produce a shared briefing
(characters, proper nouns, glossary, tone); chunk workers then translate SRT
slices concurrently against that briefing. Each stage picks an agent backend
through `services.inference` (agy / claude / codex).
"""

from .errors import ChunkTranslationError, TranslationError
from .facade import run_pre_pass, translate_chunks
from .request import TranslationRequest

__all__ = [
    "run_pre_pass",
    "translate_chunks",
    "TranslationRequest",
    "TranslationError",
    "ChunkTranslationError",
]
