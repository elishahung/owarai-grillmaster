"""Shared output enforcement for the backends behind ``run_inference``.

Two layers of validation, one loop:

* **schema** — the output must parse as the caller's Pydantic model. No
  backend has native structured output, so ``run_inference`` appends the
  JSON Schema to the prompt.
* **caller invariants the schema cannot express** — an optional ``validate``
  hook (e.g. "one ``segment_summary`` per chunk boundary": any list length is
  schema-valid, so only the caller can judge it).

Either failure re-prompts with the error and the prior output until the output
is accepted or the retry budget is spent.
"""

from __future__ import annotations

import json
from typing import Callable

from loguru import logger
from pydantic import BaseModel

from .base import InferenceError


class SchemaValidationError(InferenceError):
    """Raised when a backend cannot produce valid output in budget."""


# Validate-and-repair attempt cap, shared by every backend. Hardcoded
# maintainer constant — each retry is a real model request, so this is
# deliberately small and not exposed as config.
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


def enforce_schema[T: BaseModel](
    invoke_once: Callable[..., str],
    *,
    schema: type[T],
    base_prompt: str,
    validate: Callable[[T], None] | None = None,
    max_retries: int = MAX_SCHEMA_RETRIES,
) -> T:
    """Validate-and-repair loop around a single-shot backend invocation.

    `invoke_once(prompt, repairing=...)` runs one backend round; `repairing` is
    True on every round after the first, so the caller can drop inputs a
    format fix does not need (audio). `validate(parsed)` may raise
    `ValueError` to reject an output that parses but breaks a caller invariant;
    its message is fed back verbatim as the repair instruction, so make it name
    exactly what is missing. Returns the accepted, parsed model.
    Raises `SchemaValidationError` if nothing is accepted within
    `max_retries`.
    """
    last_error: ValueError | None = None
    repair = ""
    for attempt in range(1, max_retries + 1):
        text = invoke_once(base_prompt + repair, repairing=attempt > 1)
        cleaned = extract_json_object(text)
        try:
            parsed = schema.model_validate_json(cleaned)
            if validate is not None:
                validate(parsed)
        # pydantic's ValidationError is a ValueError, so one clause covers both
        # the schema failure and the caller invariant.
        except ValueError as e:
            last_error = e
            logger.warning(
                f"[schema] output rejected "
                f"(attempt {attempt}/{max_retries}): {e}"
            )
            repair = (
                "\n\n【修正要求】你上一次的回應未通過輸出驗證。"
                f"驗證錯誤：\n{e}\n\n"
                "你上一次（無效）的輸出為：\n"
                f"{text[:8000]}\n\n"
                "請只輸出一個符合 schema 的修正後 JSON 物件，"
                "不要任何說明文字或 markdown code fence。"
            )
            continue
        return parsed

    raise SchemaValidationError(
        f"output failed validation after {max_retries} attempts: {last_error}"
    )
