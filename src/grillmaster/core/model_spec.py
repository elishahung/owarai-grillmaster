"""Agent backend + model + reasoning effort, written `backend/model[/effort]`.

Lives in core because both `config` (parses it from `grill.toml`) and
`agents` (resolves adapters from it) need it, and `agents` may not import
`config`. Each adapter maps `Effort` onto its own CLI vocabulary. `Role` is
the key `grill.toml` `[agents.roles]` maps onto a `ModelSpec`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


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
    UTILITY = "utility"  # titles, date research
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


@dataclass(frozen=True, slots=True)
class ModelSpec:
    backend: Backend
    model: str
    effort: Effort

    def __post_init__(self) -> None:
        if not re.fullmatch(MODEL_NAME_PATTERN, self.model):
            raise ValueError(f"Invalid model name: {self.model!r}")

    @classmethod
    def parse(cls, text: str) -> ModelSpec:
        """Parse `backend/model` or `backend/model/effort`, written exactly.

        No whitespace, lowercase effort; a spec without one gets `DEFAULT_EFFORT`.
        """
        match text.split("/"):
            case [backend_text, model] if backend_text and model:
                effort_text = None
            case [backend_text, model, effort_text] if (
                backend_text and model and effort_text
            ):
                pass
            case _:
                raise ValueError(
                    "model spec must be written as 'backend/model' or "
                    f"'backend/model/effort', got: {text!r}"
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
        return cls(backend, model, effort)

    def __str__(self) -> str:
        return f"{self.backend}/{self.model}/{self.effort}"
