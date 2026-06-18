"""Shared JSON-Schema enforcement for prompt-based backends.

Backends without native structured-output guarantees (gemini-cli, codex,
claude) all enforce a Pydantic schema the same way: append the JSON Schema to
the prompt, then validate-and-repair — on a validation failure, re-prompt with
the error and the prior (invalid) output until it parses or the retry budget is
spent. This module owns that loop so every backend shares one implementation.
"""

from __future__ import annotations

import json
from typing import Callable

from loguru import logger
from pydantic import BaseModel, ValidationError

from .base import InferenceError


class SchemaValidationError(InferenceError):
    """Raised when a backend cannot produce schema-valid output in budget."""


# Schema validate-and-repair attempt cap, shared by every prompt-based backend
# (gemini-cli / codex / claude). Hardcoded maintainer constant — each retry is a
# real model request, so this is deliberately small and not exposed as config.
MAX_SCHEMA_RETRIES = 3


_SCHEMA_INSTRUCTION = (
    "\n\n【輸出要求】只輸出一個符合下列 JSON Schema 的 JSON 物件，"
    "不要任何說明文字、前後綴或 markdown code fence：\n{schema_json}"
)


def schema_instruction(schema: type[BaseModel]) -> str:
    """The prompt suffix instructing the model to emit JSON for `schema`."""
    return _SCHEMA_INSTRUCTION.format(
        schema_json=json.dumps(schema.model_json_schema(), ensure_ascii=False)
    )


def extract_json_object(text: str) -> str:
    """Best-effort extraction of a single JSON object from model output.

    Tolerates ```json fences and surrounding prose. Returns the substring from
    the first ``{`` to the last ``}``; if no braces are present the stripped
    input is returned so the caller's parser raises a meaningful error.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        without_open = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        if without_open.rstrip().endswith("```"):
            without_open = without_open.rstrip()[:-3]
        stripped = without_open.strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return stripped[start : end + 1]
    return stripped


def enforce_schema(
    invoke_once: Callable[[str], tuple[str, int]],
    *,
    schema: type[BaseModel],
    base_prompt: str,
    max_retries: int = MAX_SCHEMA_RETRIES,
) -> tuple[str, int]:
    """Validate-and-repair loop around a single-shot backend invocation.

    `invoke_once(prompt) -> (raw_text, requests)` runs one backend round.
    Returns `(validated_json_text, total_requests)` where the text is
    guaranteed-parseable for `schema`. Raises `SchemaValidationError` if no
    attempt validates within `max_retries`.
    """
    total_requests = 0
    last_error: ValidationError | None = None
    repair = ""
    for attempt in range(1, max_retries + 1):
        response, requests = invoke_once(base_prompt + repair)
        total_requests += requests
        cleaned = extract_json_object(response)
        try:
            schema.model_validate_json(cleaned)
        except ValidationError as ve:
            last_error = ve
            logger.warning(
                f"[schema] validation failed "
                f"(attempt {attempt}/{max_retries}): {ve}"
            )
            repair = (
                "\n\n【修正要求】你上一次的回應未通過 JSON schema 驗證。"
                f"驗證錯誤：\n{ve}\n\n"
                "你上一次（無效）的輸出為：\n"
                f"{response[:8000]}\n\n"
                "請只輸出一個符合 schema 的修正後 JSON 物件，"
                "不要任何說明文字或 markdown code fence。"
            )
            continue
        return cleaned, total_requests

    raise SchemaValidationError(
        f"output failed schema validation after {max_retries} attempts: "
        f"{last_error}"
    )
