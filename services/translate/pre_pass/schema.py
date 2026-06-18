"""Pre-pass result schema — pydantic-only so a standalone validator subprocess
can import it without dragging in settings, genai, or media dependencies."""

from __future__ import annotations

from pydantic import BaseModel


class Character(BaseModel):
    name_jp: str
    name_zh: str
    role_note: str


class Catchphrase(BaseModel):
    phrase_jp: str
    phrase_zh: str
    note: str


class SegmentSummary(BaseModel):
    from_index: int
    to_index: int
    summary: str


class PrePassResult(BaseModel):
    summary: str
    characters: list[Character]
    proper_nouns: dict[str, str]
    glossary: dict[str, str]
    catchphrases: list[Catchphrase]
    tone_notes: str
    segment_summaries: list[SegmentSummary]
