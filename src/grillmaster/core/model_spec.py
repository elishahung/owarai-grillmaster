"""Agent backend + model + reasoning effort, written `backend/model[/effort]`.

Lives in core because both `config` (parses it from `grill.toml`) and
`agents` (resolves adapters from it) need it, and `agents` may not import
`config`. Each adapter maps `Effort` onto its own CLI vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Backend(StrEnum):
    AGY = "agy"
    CODEX = "codex"
    CLAUDE = "claude"


class Effort(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXTRA = "extra"
    MAX = "max"
    ULTRA = "ultra"


# Applied by `parse` when the spec string names no effort.
DEFAULT_EFFORT = Effort.HIGH


@dataclass(frozen=True, slots=True)
class ModelSpec:
    backend: Backend
    model: str
    effort: Effort

    def __post_init__(self) -> None:
        if not self.model or "/" in self.model:
            raise ValueError(f"Invalid model name: {self.model!r}")

    @classmethod
    def parse(cls, text: str) -> ModelSpec:
        """Parse `backend/model` or `backend/model/effort`.

        Whitespace around segments is ignored, the effort is case-insensitive,
        and a missing or empty effort becomes `DEFAULT_EFFORT`.
        """
        match [part.strip() for part in text.split("/")]:
            case [backend_text, model] if backend_text and model:
                effort_text = ""
            case [backend_text, model, effort_text] if backend_text and model:
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
            effort = Effort(effort_text.lower()) if effort_text else DEFAULT_EFFORT
        except ValueError:
            raise ValueError(
                f"Unknown effort {effort_text!r} in {text!r}; "
                f"expected one of: {', '.join(Effort)}"
            ) from None
        return cls(backend, model, effort)

    def __str__(self) -> str:
        return f"{self.backend}/{self.model}/{self.effort}"
