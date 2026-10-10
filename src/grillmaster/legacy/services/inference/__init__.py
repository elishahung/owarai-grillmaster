"""Unified model-inference layer.

`run_inference` is the single entry point; it dispatches to a concrete agent
backend (Antigravity CLI / Codex CLI / Claude Agent SDK). See `base.py` for the
call contract — `schema`, `cwd`, and `audio` parameterize one call rather than
splitting it into separate "agentic" and "inference" functions.
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Callable, overload

from loguru import logger
from pydantic import BaseModel

from .base import (
    Backend,
    InferenceError,
    InferenceNotInstalledError,
    InferenceQuotaError,
    UnsupportedMediaError,
    backend_supports_audio,
    truncate_middle,
)
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
    "SchemaValidationError",
    "UnsupportedMediaError",
    "backend_supports_audio",
    "run_inference",
    "run_codex_exec",
    "run_claude_sdk_exec",
    "run_agy",
    "CodexInvocationError",
    "CodexNotInstalledError",
    "ClaudeSDKExecError",
    "ClaudeSDKNotInstalledError",
    "ClaudeSDKRateLimitError",
    "AgyError",
    "AgyNotInstalledError",
    "AgyQuotaError",
]


_REPAIR_WITHOUT_AUDIO_NOTE = (
    "\n\n（本輪修正不再附上音訊；請沿用你上一輪聆聽得到的結論，只修正輸出。）"
)


@contextmanager
def _working_dir(cwd: Path | None):
    """Yield `cwd`, or a throwaway temp dir when none is supplied."""
    if cwd is not None:
        yield cwd
    else:
        with tempfile.TemporaryDirectory(prefix="inference_") as tmp:
            yield Path(tmp)


@overload
def run_inference(
    *,
    backend: Backend,
    prompt: str,
    cwd: Path | None = None,
    images: list[Path] | None = None,
    audio: list[Path] | None = None,
    schema: None = None,
    model: str | None = None,
    reasoning_effort: str = "high",
) -> str: ...


@overload
def run_inference[T: BaseModel](
    *,
    backend: Backend,
    prompt: str,
    cwd: Path | None = None,
    images: list[Path] | None = None,
    audio: list[Path] | None = None,
    schema: type[T],
    validate: Callable[[T], None] | None = None,
    model: str | None = None,
    reasoning_effort: str = "high",
) -> T: ...


def run_inference(
    *,
    backend: Backend,
    prompt: str,
    cwd: Path | None = None,
    images: list[Path] | None = None,
    audio: list[Path] | None = None,
    schema: type[BaseModel] | None = None,
    validate: Callable[[BaseModel], None] | None = None,
    model: str | None = None,
    reasoning_effort: str = "high",
) -> str | BaseModel:
    """Run `prompt` through `backend`.

    Every backend is a single-shot `prompt → text` agent with its built-in web
    tools enabled. Without `schema` the model's raw final message is returned
    (agentic file-writing callers pass `cwd` and inspect files after). With
    `schema` its JSON-Schema instruction is appended, the output goes through
    the `enforce_schema` validate-and-repair loop, and the parsed model is
    returned. Repair rounds re-send the prompt and images but not the audio. `validate` (which requires `schema`) rejects output that parses
    but breaks a caller invariant the schema cannot express — a list that must
    hold one entry per input range, say — and its `ValueError` message is fed
    back as the repair instruction.
    """
    backend = Backend(backend)

    if audio and not backend_supports_audio(backend):
        raise UnsupportedMediaError(
            f"backend {backend.value!r} cannot ingest audio "
            f"({len(audio)} file(s) given)"
        )

    # codex/claude fall back to their own default model; agy has none.
    if backend == Backend.AGY and not model:
        raise InferenceError(f"backend {backend.value!r} requires an explicit model")

    if validate is not None and schema is None:
        raise InferenceError("validate= requires a schema to validate against")

    full_prompt = prompt
    if schema is not None:
        full_prompt += schema_instruction(schema)

    # codex/claude need a working dir for their file tools; agy manages its own
    # staged workspace, so it gets no work dir.
    needs_workdir = backend in (Backend.CODEX, Backend.CLAUDE)
    with _working_dir(cwd) if needs_workdir else nullcontext(None) as work:

        def invoke_once(p: str, *, repairing: bool = False) -> str:
            if backend == Backend.AGY:
                # A schema-repair round fixes the previous output's format;
                # re-listening to the audio would only burn quota.
                if repairing and audio:
                    p += _REPAIR_WITHOUT_AUDIO_NOTE
                # model + effort are mapped to agy's --model string inside the
                # wrapper; the prompt, images, and audio are staged to files.
                return run_agy(
                    p,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    images=images,
                    audio=None if repairing else audio,
                    cwd=cwd,
                )
            runner = run_codex_exec if backend == Backend.CODEX else run_claude_sdk_exec
            return runner(
                prompt=p,
                cwd=work,
                images=images,
                model=model,
                reasoning_effort=reasoning_effort,
            )

        if schema is None:
            text = invoke_once(full_prompt)
            result: str | BaseModel = text
        else:
            result = enforce_schema(
                invoke_once,
                schema=schema,
                base_prompt=full_prompt,
                validate=validate,
            )
            text = result.model_dump_json()

    # ONE final-message log site for every backend. Each backend's only job is
    # `prompt -> text`; logging (with middle-truncation so large SRT/JSON output
    # doesn't flood the log) lives here, not duplicated and diverging per backend.
    logger.debug(f"{backend.value} final message:\n{truncate_middle(text)}")
    return result
