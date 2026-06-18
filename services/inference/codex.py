"""Subprocess wrapper for the Codex CLI."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from loguru import logger

from .base import AgentExecError


class CodexInvocationError(AgentExecError):
    """Raised when `codex exec` exits non-zero or times out."""


class CodexNotInstalledError(CodexInvocationError):
    """Raised when the configured Codex executable is not on PATH."""


# Default per-invocation timeout for `codex exec`. Hardcoded maintainer constant.
_DEFAULT_TIMEOUT_SECS = 900
# Codex model / reasoning effort used when a caller does not pass them.
_DEFAULT_MODEL = "gpt-5.5"
_DEFAULT_REASONING_EFFORT = "high"


def run_codex_exec(
    prompt: str,
    cwd: Path,
    images: list[Path] | None = None,
    output_last_message_path: Path | None = None,
    timeout: int | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> str:
    """Invoke `codex exec` non-interactively and return the final assistant message."""
    executable = shutil.which("codex")
    if executable is None:
        raise CodexNotInstalledError(
            "Codex executable not found on PATH: 'codex'"
        )

    abs_cwd = cwd.resolve()
    effective_timeout = timeout or _DEFAULT_TIMEOUT_SECS
    effective_model = model or _DEFAULT_MODEL
    effective_effort = (reasoning_effort or _DEFAULT_REASONING_EFFORT).lower()

    if output_last_message_path is not None:
        capture_path = output_last_message_path.resolve()
        capture_path.parent.mkdir(parents=True, exist_ok=True)
        cleanup_capture = False
    else:
        handle = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        )
        handle.close()
        capture_path = Path(handle.name)
        cleanup_capture = True

    cmd: list[str] = [
        executable,
        "exec",
        "-m",
        effective_model,
        "-c",
        f"model_reasoning_effort={effective_effort}",
        "--cd",
        str(abs_cwd),
        "--yolo",
        "--output-last-message",
        str(capture_path),
    ]
    for img in images or []:
        cmd += ["--image", str(img.resolve())]
    cmd.append("--")

    logger.debug(
        f"Running codex exec: argv={cmd} "
        f"prompt_chars={len(prompt)} (via stdin) "
        f"timeout={effective_timeout}s"
    )

    try:
        try:
            result = subprocess.run(
                cmd,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=effective_timeout,
                capture_output=True,
                input=prompt,
            )
        except subprocess.TimeoutExpired as exc:
            raise CodexInvocationError(
                f"codex exec timed out after {effective_timeout}s"
            ) from exc

        if result.returncode != 0:
            stderr_tail = "\n".join(
                (result.stderr or "").strip().splitlines()[-20:]
            )
            raise CodexInvocationError(
                f"codex exec exited with code {result.returncode}: {stderr_tail}"
            )

        if capture_path.exists() and capture_path.stat().st_size > 0:
            final_message = capture_path.read_text(
                encoding="utf-8", errors="replace"
            )
        else:
            final_message = result.stdout or ""
        # The final message is logged centrally by `run_inference` (one site for
        # every backend, with middle-truncation), not here.
        return final_message
    finally:
        if cleanup_capture:
            try:
                capture_path.unlink(missing_ok=True)
            except OSError:
                pass
