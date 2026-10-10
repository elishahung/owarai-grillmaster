"""The pre-pass briefing: whole-film context every translation step shares.

`Briefing` is the pre-pass agent's structured output, so it stays strict-JSON
compatible: every field is required, objects forbid extra keys, and there are
no free-key maps (`proper_nouns` / `glossary` are `TermMapping` lists).
Prompts still see those two as `{source: target}` objects, the shape the
translation prompts describe.
"""

from __future__ import annotations

import json
from collections import Counter

from pydantic import BaseModel, ConfigDict, field_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Character(_Strict):
    name_jp: str
    name_zh: str
    role_note: str


class TermMapping(_Strict):
    """One `source -> target` rendering agreed for the whole episode."""

    source: str
    target: str


class Catchphrase(_Strict):
    phrase_jp: str
    phrase_zh: str
    note: str


class SegmentSummary(_Strict):
    """What happens inside one chunk range (SRT indexes, inclusive)."""

    from_index: int
    to_index: int
    summary: str


class Briefing(_Strict):
    summary: str
    characters: list[Character]
    proper_nouns: list[TermMapping]
    glossary: list[TermMapping]
    catchphrases: list[Catchphrase]
    tone_notes: str
    segment_summaries: list[SegmentSummary]

    @field_validator("proper_nouns", "glossary")
    @classmethod
    def _reject_conflicting_terms(cls, terms: list[TermMapping]) -> list[TermMapping]:
        # The prompt view is a `{source: target}` object, so one source cannot
        # carry two renderings. Exact repeats collapse harmlessly.
        distinct = {(term.source, term.target) for term in terms}
        counts = Counter(source for source, _ in distinct)
        conflicts = sorted(source for source, count in counts.items() if count > 1)
        if conflicts:
            raise ValueError(
                f"conflicting targets for the same source term: {', '.join(conflicts)}"
            )
        return terms

    def segment_summary_for(
        self, from_index: int, to_index: int
    ) -> SegmentSummary | None:
        """The summary written for exactly this chunk range, if any."""
        for segment in self.segment_summaries:
            if segment.from_index == from_index and segment.to_index == to_index:
                return segment
        return None

    def prompt_dict(self, *, chunk: tuple[int, int] | None = None) -> dict[str, object]:
        """The briefing as prompts see it, term lists folded into objects.

        Keys keep the field order, so the full view renders byte-identical to
        the legacy `pre_pass.json`. With `chunk=(from_index, to_index)` the
        per-range list is replaced in place by that range's `segment_summary`
        (empty when the briefing has none), which is what a chunk translator
        receives.
        """
        view: dict[str, object] = self.model_dump()
        view["proper_nouns"] = _term_dict(self.proper_nouns)
        view["glossary"] = _term_dict(self.glossary)
        if chunk is None:
            return view
        segment = self.segment_summary_for(*chunk)
        chunk_view: dict[str, object] = {}
        for key, value in view.items():
            if key == "segment_summaries":
                chunk_view["segment_summary"] = segment.summary if segment else ""
            else:
                chunk_view[key] = value
        return chunk_view

    def render_for_prompt(self, *, chunk: tuple[int, int] | None = None) -> str:
        """Indented JSON text of `prompt_dict`."""
        return json.dumps(self.prompt_dict(chunk=chunk), ensure_ascii=False, indent=2)


def _term_dict(terms: list[TermMapping]) -> dict[str, str]:
    return {term.source: term.target for term in terms}
