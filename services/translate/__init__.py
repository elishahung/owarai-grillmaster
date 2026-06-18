"""Subtitle translation pipeline.

A pre-pass stage scans the full SRT once to produce a shared briefing
(characters, proper nouns, glossary, tone); chunk workers then translate SRT
slices concurrently against that briefing. Each stage picks a backend through
`services.inference` (gemini-api / gemini-cli / claude / codex).
"""

from .errors import (
    ChunkTranslationError,
    GeminiTranslationError,
    PrePassError,
    TranslationCostSummary,
    TranslationError,
)
from .facade import Translate, TranslationResult
from .request import TranslationRequest

__all__ = [
    "Translate",
    "TranslationRequest",
    "TranslationResult",
    "TranslationError",
    "GeminiTranslationError",
    "TranslationCostSummary",
    "PrePassError",
    "ChunkTranslationError",
]
