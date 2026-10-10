from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from grillmaster.core.briefing import (
    Briefing,
    Catchphrase,
    Character,
    SegmentSummary,
    TermMapping,
)


@pytest.fixture
def briefing() -> Briefing:
    return Briefing(
        summary="漫才大會決賽。",
        characters=[
            Character(name_jp="盛山晋太郎", name_zh="盛山晉太郎", role_note="吐槽")
        ],
        proper_nouns=[
            TermMapping(source="森山", target="盛山"),
            TermMapping(source="コロチキ", target="KoroChiki"),
        ],
        glossary=[TermMapping(source="ボケ", target="裝傻")],
        catchphrases=[
            Catchphrase(phrase_jp="なんでやねん", phrase_zh="為什麼啦", note="")
        ],
        tone_notes="輕鬆",
        segment_summaries=[
            SegmentSummary(from_index=1, to_index=40, summary="開場"),
            SegmentSummary(from_index=41, to_index=80, summary="決賽"),
        ],
    )


def _walk_objects(schema: dict[str, Any]) -> list[dict[str, Any]]:
    objects = [schema] if schema.get("type") == "object" else []
    for definition in schema.get("$defs", {}).values():
        objects.extend(_walk_objects(definition))
    return objects


def test_json_schema_is_strict_mode_compatible():
    schema = Briefing.model_json_schema()
    objects = _walk_objects(schema)
    assert {obj["title"] for obj in objects} == {
        "Briefing",
        "Character",
        "TermMapping",
        "Catchphrase",
        "SegmentSummary",
    }
    for obj in objects:
        assert obj["additionalProperties"] is False, obj["title"]
        assert set(obj["required"]) == set(obj["properties"]), obj["title"]
        for prop in obj["properties"].values():
            # No free-key maps anywhere.
            assert prop.get("type") != "object" or "properties" in prop


def test_extra_keys_are_rejected(briefing: Briefing):
    data = briefing.model_dump()
    data["characters"][0]["nickname"] = "x"
    with pytest.raises(ValidationError):
        Briefing.model_validate(data)


def test_conflicting_term_targets_are_rejected(briefing: Briefing):
    data = briefing.model_dump()
    data["glossary"].append({"source": "ボケ", "target": "耍笨"})
    with pytest.raises(ValidationError, match="ボケ"):
        Briefing.model_validate(data)


def test_exact_duplicate_terms_are_accepted(briefing: Briefing):
    data = briefing.model_dump()
    data["proper_nouns"].append({"source": "森山", "target": "盛山"})
    parsed = Briefing.model_validate(data)
    assert parsed.prompt_dict()["proper_nouns"] == {
        "森山": "盛山",
        "コロチキ": "KoroChiki",
    }


def test_full_render_matches_legacy_pre_pass_json(briefing: Briefing):
    # Legacy prompts embedded pre_pass.json verbatim: the PrePassResult dump with
    # `proper_nouns` / `glossary` as `{source: target}` objects.
    legacy = {
        "summary": "漫才大會決賽。",
        "characters": [
            {"name_jp": "盛山晋太郎", "name_zh": "盛山晉太郎", "role_note": "吐槽"}
        ],
        "proper_nouns": {"森山": "盛山", "コロチキ": "KoroChiki"},
        "glossary": {"ボケ": "裝傻"},
        "catchphrases": [
            {"phrase_jp": "なんでやねん", "phrase_zh": "為什麼啦", "note": ""}
        ],
        "tone_notes": "輕鬆",
        "segment_summaries": [
            {"from_index": 1, "to_index": 40, "summary": "開場"},
            {"from_index": 41, "to_index": 80, "summary": "決賽"},
        ],
    }
    assert briefing.render_for_prompt() == json.dumps(
        legacy, ensure_ascii=False, indent=2
    )


def test_chunk_render_matches_legacy_chunk_briefing(briefing: Briefing):
    rendered = briefing.render_for_prompt(chunk=(41, 80))
    view = json.loads(rendered)
    assert list(view) == [
        "summary",
        "characters",
        "proper_nouns",
        "glossary",
        "catchphrases",
        "tone_notes",
        "segment_summary",
    ]
    assert view["segment_summary"] == "決賽"
    assert "  " in rendered  # indented like the legacy chunk prompt


def test_chunk_render_without_matching_segment_is_empty(briefing: Briefing):
    assert briefing.prompt_dict(chunk=(1, 41))["segment_summary"] == ""


def test_segment_summary_for(briefing: Briefing):
    assert briefing.segment_summary_for(1, 40) == SegmentSummary(
        from_index=1, to_index=40, summary="開場"
    )
    assert briefing.segment_summary_for(1, 80) is None


def test_round_trips_through_json(briefing: Briefing):
    assert Briefing.model_validate_json(briefing.model_dump_json()) == briefing
