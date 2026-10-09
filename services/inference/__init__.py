"""Unified model-inference layer.

`run_inference` is the single entry point; it dispatches to a concrete backend
(Gemini API / Antigravity CLI / Codex CLI / Claude Agent SDK). See `base.py` for the
call contract — `schema`, `cwd`, and `audio` parameterize one call rather than
splitting it into separate "agentic" and "inference" functions.
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Callable

from loguru import logger
from pydantic import BaseModel

from .base import (
    Backend,
    InferenceError,
    InferenceNotInstalledError,
    InferenceQuotaError,
    UnsupportedMediaError,
    backend_supports_audio,
    fan_out_concurrency,
    is_agent_backend,
    is_gemini_backend,
    truncate_middle,
)
from .result import InferenceResult
from .schema_enforce import (
    SchemaValidationError,
    enforce_schema,
    schema_instruction,
)
from .codex import (
    CodexInvocationError,
    CodexNotInstalledError,
    run_codex_exec,
)
from .claude_sdk import (
    ClaudeSDKExecError,
    ClaudeSDKNotInstalledError,
    ClaudeSDKRateLimitError,
    run_claude_sdk_exec,
)
from .gemini_api import GeminiApiError, run_gemini_api
from .agy import (
    AgyError,
    AgyNotInstalledError,
    AgyQuotaError,
    run_agy,
)

__all__ = [
    "Backend",
    "InferenceError",
    "InferenceNotInstalledError",
    "InferenceQuotaError",
    "InferenceResult",
    "SchemaValidationError",
    "UnsupportedMediaError",
    "backend_supports_audio",
    "fan_out_concurrency",
    "is_agent_backend",
    "is_gemini_backend",
    "run_inference",
    "run_codex_exec",
    "run_claude_sdk_exec",
    "run_gemini_api",
    "run_agy",
    "CodexInvocationError",
    "CodexNotInstalledError",
    "ClaudeSDKExecError",
    "ClaudeSDKNotInstalledError",
    "ClaudeSDKRateLimitError",
    "GeminiApiError",
    "AgyError",
    "AgyNotInstalledError",
    "AgyQuotaError",
]


@contextmanager
def _working_dir(cwd: Path | None):
    """Yield `cwd`, or a throwaway temp dir when none is supplied."""
    if cwd is not None:
        yield cwd
    else:
        with tempfile.TemporaryDirectory(prefix="inference_") as tmp:
            yield Path(tmp)


def _run_with_schema(
    invoke_once,
    *,
    base_prompt: str,
    schema: type[BaseModel] | None,
    validate,
) -> InferenceResult:
    """One shot when no schema is requested, the repair loop otherwise."""
    if schema is None:
        return invoke_once(base_prompt)
    return enforce_schema(
        invoke_once,
        schema=schema,
        base_prompt=base_prompt,
        validate=validate,
    )


def run_inference(
    *,
    backend: Backend,
    prompt: str,
    system_prompt: str | None = None,
    cwd: Path | None = None,
    images: list[Path] | None = None,
    audio: list[Path] | None = None,
    schema: type[BaseModel] | None = None,
    validate: Callable[[BaseModel], None] | None = None,
    model: str | None = None,
    reasoning_effort: str = "high",
    output_last_message_path: Path | None = None,
    timeout: int | None = None,
    web_search: bool = False,
) -> InferenceResult:
    """Run `prompt` through `backend`, returning an `InferenceResult`.

    ``web_search=True`` ensures the backend's built-in web tools are enabled
    for this call (agent backends only): codex gets ``tools.web_search=true``,
    and claude/agy already expose them under their permission bypass.

    Every backend's only job is `prompt → text`; validation lives HERE, so the
    two branches differ solely in how the schema reaches the model:

    * **gemini-api** enforces a schema natively (`response_json_schema`) and is
      the sole metered backend.
    * **agy / codex / claude** are single-shot text
      generators, so the JSON-Schema instruction is appended to the prompt.

    Both then run through the `enforce_schema` validate-and-repair loop.
    `validate` (which requires `schema`) rejects output that parses but breaks a
    caller invariant the schema cannot express — a list that must hold one
    entry per input range, say — and its `ValueError` message is fed back as
    the repair instruction.

    See `services.inference.base` for the full contract. When `schema` is given
    the result `.text` is guaranteed-parseable JSON for that model.
    """
    backend = Backend(backend)

    if audio and not backend_supports_audio(backend):
        raise UnsupportedMediaError(
            f"backend {backend.value!r} cannot ingest audio "
            f"({len(audio)} file(s) given)"
        )

    if is_gemini_backend(backend) and not model:
        raise InferenceError(
            f"backend {backend.value!r} requires an explicit model"
        )

    if web_search and not is_agent_backend(backend):
        raise InferenceError(
            f"backend {backend.value!r} has no built-in web-search tool"
        )

    if validate is not None and schema is None:
        raise InferenceError("validate= requires a schema to validate against")

    # gemini-api: native schema + metered cost + raw system_instruction, so the
    # prompt carries no JSON-Schema suffix.
    if backend == Backend.GEMINI_API:

        def invoke_api(p: str) -> InferenceResult:
            return run_gemini_api(
                prompt=p,
                system_prompt=system_prompt,
                images=images,
                audio=audio,
                schema=schema,
                model=model,
                reasoning_effort=reasoning_effort,
                timeout=timeout,
            )

        result = _run_with_schema(
            invoke_api, base_prompt=prompt, schema=schema, validate=validate
        )
    else:
        # Prompt-based backends: system prompt and user prompt are one
        # concatenated string, with the JSON-Schema instruction appended below.
        full_prompt = (
            f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        )
        # codex/claude need a working dir for their file tools; agy is an agent
        # too but manages its own staged workspace, so it gets no work dir.
        needs_workdir = backend in (Backend.CODEX, Backend.CLAUDE)
        work_ctx = _working_dir(cwd) if needs_workdir else nullcontext(None)
        with work_ctx as work:

            def invoke_once(p: str) -> InferenceResult:
                if backend == Backend.AGY:
                    # The prompt, images, and audio are staged to files; model +
                    # effort are mapped to agy's --model string inside the wrapper.
                    agy = run_agy(
                        p,
                        model=model,
                        reasoning_effort=reasoning_effort,
                        images=images,
                        audio=audio,
                        cwd=cwd,
                        timeout=timeout,
                    )
                    return InferenceResult(
                        text=agy.response, requests=agy.requests
                    )
                runner = (
                    run_codex_exec
                    if backend == Backend.CODEX
                    else run_claude_sdk_exec
                )
                text = runner(
                    prompt=p,
                    cwd=work,
                    images=images,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    output_last_message_path=output_last_message_path,
                    timeout=timeout,
                    web_search=web_search,
                )
                return InferenceResult(text=text)

            result = _run_with_schema(
                invoke_once,
                base_prompt=full_prompt
                + (schema_instruction(schema) if schema else ""),
                schema=schema,
                validate=validate,
            )

    # ONE final-message log site for every backend. Each backend's only job is
    # `prompt -> text`; logging (with middle-truncation so large SRT/JSON output
    # doesn't flood the log) lives here, not duplicated and diverging per backend.
    logger.debug(
        f"{backend.value} final message:\n{truncate_middle(result.text)}"
    )
    return result
