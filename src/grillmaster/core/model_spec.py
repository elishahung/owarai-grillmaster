"""Agent backend + model + reasoning effort + fast mode, written
`backend/model[/effort][/fast]`.

Lives in core because both `config` (parses it from `grill.toml`) and
`agents` (resolves adapters from it) need it, and `agents` may not import
`config`. Each adapter maps `Effort` onto its own CLI vocabulary; `fast`
(a faster tier that spends more quota) needs the `FAST_MODE` capability. `Role` is
the key `grill.toml` `[agents.roles]` maps onto a `ModelSpec`;
`ModelSpecField` is the pydantic field written as the spec string
(`grill.toml`, the tool-session manifest).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from pydantic import PlainSerializer, PlainValidator, WithJsonSchema


class Backend(StrEnum):
    AGY = "agy"
    GEMINI = "gemini"
    CODEX = "codex"
    CLAUDE = "claude"


class Role(StrEnum):
    """What an agent call is for; each role resolves to one `ModelSpec`."""

    PREPASS = "prepass"
    CHUNK = "chunk"
    POSTPROCESS = "postprocess"
    PROOFREAD = "proofread"  # optional final proofread after the glossary check
    TRANSCRIBE = "transcribe"  # the `transcribe` tool: watches a video span
    UTILITY = "utility"  # titles, date research, pre-pass/chunk handoff repairs
    CHAT = "chat"  # live-chat translation
    IMAGE = "image"  # cover generation; needs image-generation capability


class Effort(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXTRA = "extra"
    MAX = "max"
    ULTRA = "ultra"


# Applied by `parse` when the spec string names no effort.
DEFAULT_EFFORT = Effort.HIGH

# A model name is one path segment without whitespace.
MODEL_NAME_PATTERN = r"[^/\s]+"

# The optional last segment that turns fast mode on.
FAST = "fast"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    backend: Backend
    model: str
    effort: Effort
    fast: bool = False

    def __post_init__(self) -> None:
        if not re.fullmatch(MODEL_NAME_PATTERN, self.model):
            raise ValueError(f"Invalid model name: {self.model!r}")

    @classmethod
    def parse(cls, text: str) -> ModelSpec:
        """Parse `backend/model[/effort][/fast]`, written exactly.

        No whitespace, lowercase effort; a spec without one gets `DEFAULT_EFFORT`.
        """
        head, _, last = text.rpartition("/")
        # `backend/fast` names a model called fast.
        fast = last == FAST and "/" in head
        match (head if fast else text).split("/"):
            case [backend_text, model] if backend_text and model:
                effort_text = None
            case [backend_text, model, effort_text] if (
                backend_text and model and effort_text
            ):
                pass
            case _:
                raise ValueError(
                    "model spec must be written as "
                    f"'backend/model[/effort][/fast]', got: {text!r}"
                )
        try:
            backend = Backend(backend_text)
        except ValueError:
            raise ValueError(
                f"Unknown backend {backend_text!r} in {text!r}; "
                f"expected one of: {', '.join(Backend)}"
            ) from None
        try:
            effort = DEFAULT_EFFORT if effort_text is None else Effort(effort_text)
        except ValueError:
            raise ValueError(
                f"Unknown effort {effort_text!r} in {text!r}; "
                f"expected one of: {', '.join(Effort)}"
            ) from None
        return cls(backend, model, effort, fast)

    def __str__(self) -> str:
        text = f"{self.backend}/{self.model}/{self.effort}"
        return f"{text}/{FAST}" if self.fast else text


def _parse_model_spec(value: object) -> ModelSpec:
    if isinstance(value, ModelSpec):
        return value
    if isinstance(value, str):
        return ModelSpec.parse(value)
    raise ValueError("model spec must be a 'backend/model[/effort][/fast]' string")


# `"backend/model[/effort][/fast]"` when serialized, a `ModelSpec` in code.
ModelSpecField = Annotated[
    ModelSpec,
    PlainValidator(_parse_model_spec),
    PlainSerializer(str, return_type=str),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": (
                f"^({'|'.join(Backend)})/{MODEL_NAME_PATTERN}"
                f"(/({'|'.join(Effort)}))?(/{FAST})?$"
            ),
            "description": (
                "backend/model[/effort][/fast]; effort defaults to high, "
                "fast (codex only) spends more quota for speed."
            ),
        }
    ),
]
