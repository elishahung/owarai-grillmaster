from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, Field

from grillmaster.agents.schema import (
    StrictSchemaError,
    json_object_answer,
    strict_json_schema,
)
from grillmaster.core.briefing import Briefing


def _objects(node: Any) -> list[dict[str, Any]]:
    """Every object schema in `node`, depth first."""
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            found.append(node)
        for value in node.values():
            found += _objects(value)
    elif isinstance(node, list):
        for item in node:
            found += _objects(item)
    return found


def test_briefing_becomes_a_strict_self_contained_schema():
    schema = strict_json_schema(Briefing)

    assert "$defs" not in schema
    assert "$ref" not in repr(schema)
    assert "title" not in repr(schema)
    objects = _objects(schema)
    assert len(objects) > 1
    for node in objects:
        assert node["additionalProperties"] is False
        assert node["required"] == list(node["properties"])
    character = schema["properties"]["characters"]["items"]
    assert character["required"] == ["name_jp", "name_zh", "role_note"]
    term = schema["properties"]["proper_nouns"]["items"]
    assert term["properties"] == {
        "source": {"type": "string"},
        "target": {"type": "string"},
    }


def test_optional_fields_stay_required_and_nullable():
    class Item(BaseModel):
        label: str = Field(description="shown")
        note: str | None = None

    schema = strict_json_schema(Item)
    assert schema["required"] == ["label", "note"]
    assert schema["properties"]["label"] == {"type": "string", "description": "shown"}
    assert schema["properties"]["note"] == {
        "anyOf": [{"type": "string"}, {"type": "null"}]
    }


def test_nested_optional_model_is_inlined():
    class Inner(BaseModel):
        value: int

    class Outer(BaseModel):
        inner: Inner | None

    inner = strict_json_schema(Outer)["properties"]["inner"]["anyOf"][0]
    assert inner == {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    }


def test_free_key_dicts_are_rejected():
    class Loose(BaseModel):
        terms: dict[str, str]

    with pytest.raises(StrictSchemaError, match=r"Loose\.terms: free-key"):
        strict_json_schema(Loose)


def test_recursive_models_are_rejected():
    class Node(BaseModel):
        children: list[Node]

    Node.model_rebuild()
    with pytest.raises(StrictSchemaError, match="recursive"):
        strict_json_schema(Node)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```\n', {"a": 1}),
        ('```\n{"a": 1}\n```', {"a": 1}),
        ('Here it is:\n```json\n{"a": 1}\n```', None),
        ("```json\n[1, 2]\n```", None),
        ("blue", None),
        ("", None),
    ],
)
def test_json_object_answer_strips_one_fence(text: str, expected: object):
    assert json_object_answer(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('AN```json\n{"a": 1}\n```', {"a": 1}),
        ('Here it is:\n```json\n{"a": 1}\n```\n', {"a": 1}),
        ('Draft:\n```json\n{"a": 0}\n``````json\n{"a": 1}\n```', {"a": 1}),
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```\nDone.', None),
    ],
)
def test_json_object_answer_with_lead_in_ignores_text_before_the_fence(
    text: str, expected: object
):
    assert json_object_answer(text, lead_in=True) == expected
