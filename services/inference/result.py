"""Uniform return type for every backend invoked through `run_inference`."""

from __future__ import annotations

from pydantic import BaseModel


class InferenceResult(BaseModel):
    """Outcome of one `run_inference` call.

    `text` is the model's final message — guaranteed-parseable JSON for the
    requested schema when one was given, otherwise the raw message. `cost` is
    USD spent (0.0 for subscription backends: gemini-cli, codex, claude).
    `requests` is the number of backend model requests consumed (including any
    schema-repair retries).
    """

    text: str
    cost: float = 0.0
    requests: int = 1
