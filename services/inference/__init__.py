"""Unified model-inference layer.

`run_inference` is the single entry point; it dispatches to a concrete backend
(Gemini API / Gemini CLI / Codex CLI / Claude Agent SDK). See `base.py` for the
call contract — `schema`, `cwd`, and `audio` parameterize one call rather than
splitting it into separate "agentic" and "inference" functions.
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager, nullcontext
from pathlib import Path

from loguru import logger
from pydantic import BaseModel

from .base import (
    AgentBackend,
    AgentExecError,
    AgentNotInstalledError,
    Backend,
    InferenceError,
    InferenceNotInstalledError,
    UnsupportedMediaError,
    backend_supports_audio,
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
from .gemini_cli import (
    GeminiCliError,
    GeminiCliNotInstalledError,
    GeminiCliQuotaError,
    run_gemini_cli,
)

__all__ = [
    "AgentBackend",
    "AgentExecError",
    "AgentNotInstalledError",
    "Backend",
    "InferenceError",
    "InferenceNotInstalledError",
    "InferenceResult",
    "SchemaValidationError",
    "UnsupportedMediaError",
    "backend_supports_audio",
    "is_agent_backend",
    "is_gemini_backend",
    "run_inference",
    "run_codex_exec",
    "run_claude_sdk_exec",
    "run_gemini_api",
    "run_gemini_cli",
    "CodexInvocationError",
    "CodexNotInstalledError",
    "ClaudeSDKExecError",
    "ClaudeSDKNotInstalledError",
    "ClaudeSDKRateLimitError",
    "GeminiApiError",
    "GeminiCliError",
    "GeminiCliNotInstalledError",
    "GeminiCliQuotaError",
]

@contextmanager
def _working_dir(cwd: Path | None):
    """Yield `cwd`, or a throwaway temp dir when none is supplied."""
    if cwd is not None:
        yield cwd
    else:
        with tempfile.TemporaryDirectory(prefix="inference_") as tmp:
            yield Path(tmp)


def run_inference(
    *,
    backend: Backend,
    prompt: str,
    system_prompt: str | None = None,
    cwd: Path | None = None,
    images: list[Path] | None = None,
    audio: list[Path] | None = None,
    schema: type[BaseModel] | None = None,
    model: str | None = None,
    reasoning_effort: str = "high",
    output_last_message_path: Path | None = None,
    timeout: int | None = None,
) -> InferenceResult:
    """Run `prompt` through `backend`, returning an `InferenceResult`.

    Two shapes only:

    * **gemini-api** enforces a schema natively (`response_json_schema`) and is
      the sole metered backend, so it has its own branch.
    * **gemini-cli / codex / claude** are single-shot text generators. Schema
      handling is identical for all three and lives HERE (not in the backends):
      the JSON-Schema instruction is appended once and `enforce_schema` runs the
      shared validate-and-repair loop. Each backend's only job is `prompt → text`.

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

    # gemini-api: native schema + metered cost + raw system_instruction.
    if backend == Backend.GEMINI_API:
        result = run_gemini_api(
            prompt=prompt,
            system_prompt=system_prompt,
            images=images,
            audio=audio,
            schema=schema,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    else:
        # Prompt-based backends (gemini-cli / codex / claude): one concatenated
        # prompt, schema (if any) enforced uniformly via enforce_schema below.
        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        # codex/claude need a working dir for their file tools; the gemini CLI is
        # an agent too but manages its own media tempdir, so it gets no work dir.
        needs_workdir = backend in (Backend.CODEX, Backend.CLAUDE)
        work_ctx = _working_dir(cwd) if needs_workdir else nullcontext(None)
        with work_ctx as work:

            def invoke_once(p: str) -> tuple[str, int]:
                if backend == Backend.GEMINI_CLI:
                    cli = run_gemini_cli(
                        p,
                        model=model,
                        media_files=[*(audio or []), *(images or [])],
                        timeout=timeout,
                    )
                    return cli.response, cli.requests
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
                )
                return text, 1

            if schema is None:
                text, requests = invoke_once(full_prompt)
            else:
                text, requests = enforce_schema(
                    invoke_once,
                    schema=schema,
                    base_prompt=full_prompt + schema_instruction(schema),
                )
        result = InferenceResult(text=text, cost=0.0, requests=requests)

    # ONE final-message log site for every backend. Each backend's only job is
    # `prompt -> text`; logging (with middle-truncation so large SRT/JSON output
    # doesn't flood the log) lives here, not duplicated and diverging per backend.
    logger.debug(
        f"{backend.value} final message:\n{truncate_middle(result.text)}"
    )
    return result
