"""Turn a pydantic model into the strict JSON Schema every native schema flag takes.

codex `--output-schema` runs OpenAI strict mode: every property is required,
every object sets `additionalProperties: false`, and free-key maps do not
exist. agy additionally wants an object at the root, and gemini, which has
no schema flag, gets the same schema in its prompt. Agent output models are
designed to fit (optional fields are `T | None` and still required), and
pydantic stays the authoritative validator; this only shapes the first round.
`json_object_answer` reads a schema answer the model wrote as text instead.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic import BaseModel

type JsonSchema = dict[str, Any]

# Annotation-only keywords: no constraint lost, and strict mode rejects some.
_DROPPED_KEYS = frozenset({"title", "default", "examples"})
_REF_PREFIX = "#/$defs/"
# One whole-message ``` / ```json fence around the answer.
_WHOLE_FENCE_RE = re.compile(r"\A```(?:json)?[ \t]*\n(.*)\n```\Z", re.DOTALL)
# A ``` / ```json fenced block.
_FENCE_RE = re.compile(r"```(?:json)?[ \t]*\n(.*?)\n```", re.DOTALL)


def json_object_answer(text: str, *, lead_in: bool = False) -> dict[str, Any] | None:
    """`text` (inside at most one ``` / ```json fence) as a JSON object, or
    `None`; whether it fits the schema is the output model's call. With
    `lead_in`, the fenced block that closes the message is the answer, and
    whatever precedes it (stray tokens, leaked reasoning) is ignored."""
    stripped = text.strip()
    if fenced := _WHOLE_FENCE_RE.match(stripped):
        stripped = fenced.group(1)
    elif lead_in and (
        closing := next(
            (m for m in _FENCE_RE.finditer(stripped) if m.end() == len(stripped)),
            None,
        )
    ):
        stripped = closing.group(1)
    try:
        value = json.loads(stripped)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


class StrictSchemaError(ValueError):
    """The model cannot be expressed as a strict schema."""


def strict_json_schema(model: type[BaseModel]) -> JsonSchema:
    """`model`'s JSON Schema with refs inlined and every object made strict.

    Raises `StrictSchemaError` for a non-object root, a free-key dict
    (`dict[str, T]`: use a list of key/value objects instead), or a
    recursive model (inlining would not terminate).
    """
    raw = model.model_json_schema()
    defs: dict[str, JsonSchema] = raw.pop("$defs", {})
    schema = _strict(raw, defs, path=model.__name__, resolving=())
    if schema.get("type") != "object":
        raise StrictSchemaError(f"{model.__name__}: the root must be an object")
    return schema


def _strict(
    node: JsonSchema,
    defs: dict[str, JsonSchema],
    *,
    path: str,
    resolving: tuple[str, ...],
) -> JsonSchema:
    if "$ref" in node:
        ref: str = node["$ref"]
        name = ref.removeprefix(_REF_PREFIX)
        if name in resolving:
            raise StrictSchemaError(f"{path}: recursive model {name!r}")
        siblings = {key: value for key, value in node.items() if key != "$ref"}
        merged = {**defs[name], **siblings}
        return _strict(merged, defs, path=path, resolving=(*resolving, name))

    out: JsonSchema = {}
    for key, value in node.items():
        if key in _DROPPED_KEYS:
            continue
        match key, value:
            case "properties", dict():
                out[key] = {
                    name: _strict(sub, defs, path=f"{path}.{name}", resolving=resolving)
                    for name, sub in value.items()
                }
            case "items", dict():
                out[key] = _strict(value, defs, path=f"{path}[]", resolving=resolving)
            case (("anyOf" | "oneOf" | "allOf"), list()):
                out[key] = [
                    _strict(sub, defs, path=path, resolving=resolving) for sub in value
                ]
            case _:
                out[key] = value

    if out.get("type") == "object":
        properties: JsonSchema | None = out.get("properties")
        extra = out.get("additionalProperties")
        if not properties or (extra is not None and extra is not False):
            raise StrictSchemaError(
                f"{path}: free-key dicts cannot be expressed in strict mode; "
                "model them as a list of objects"
            )
        out["required"] = list(properties)
        out["additionalProperties"] = False
    return out
