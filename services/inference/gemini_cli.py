"""Generic subprocess wrapper for the Gemini CLI.

A pure, media-agnostic ``text + 0..N media files -> text`` wrapper. Auth is
delegated to the CLI's cached OAuth (subscription); the parent process's
API-key env vars are scrubbed so the CLI never silently falls back to paid
API-key billing.

Unlike the genai SDK (which enforces a response schema natively), the CLI is
primitive: it has no structured-output guarantee. So when a caller passes a
``schema`` pydantic model, this wrapper appends the JSON Schema to the prompt
and drives the shared ``schema_enforce.enforce_schema`` validate-and-repair
loop, staging media into a private workspace that persists across attempts.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from loguru import logger
from pydantic import BaseModel

from .base import InferenceError, InferenceNotInstalledError
from .schema_enforce import extract_json_object

# Hardcoded per-file size guard for @path media. The CLI rejects oversized
# attachments; for this project an oversized media file means something
# upstream is wrong, so fail loudly instead of silently degrading.
_MAX_MEDIA_FILE_MB = 20

# Gemini CLI executable name and per-invocation timeout. Hardcoded — these are
# maintainer constants, not per-deployment configuration.
_CLI_EXECUTABLE = "gemini"
_CLI_TIMEOUT_SECS = 900

# API-key env vars the Gemini CLI would prefer over cached OAuth. Scrubbed so
# subscription auth is used (the whole reason for the CLI backend).
_API_KEY_ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY")


class GeminiCliError(InferenceError):
    """Raised when the Gemini CLI exits non-zero, times out, or misbehaves."""


class GeminiCliNotInstalledError(GeminiCliError, InferenceNotInstalledError):
    """Raised when the configured Gemini CLI executable is not on PATH."""


class GeminiCliQuotaError(GeminiCliError):
    """Raised when the CLI reports a 429 / daily-quota-exhausted error."""


class GeminiCliResult(BaseModel):
    """Outcome of one ``run_gemini_cli`` call.

    ``requests`` is the total number of backend model requests consumed
    (summed across the CLI's internal auto-retries and any schema-repair
    attempts this wrapper made).
    """

    response: str
    requests: int
    stats: dict
    raw_envelope: dict


def extract_request_count(envelope: dict) -> int:
    """Defensively sum backend request counts from the CLI JSON envelope.

    The documented path is ``stats.models.<model>.api.totalRequests`` but
    that comes from gemini-cli issues/PRs, not a stable contract. Try the
    documented path first; if nothing is found, recursively scan for any
    ``totalRequests`` integer. Never returns < 1 for a successful call.
    """
    total = 0
    found = False

    models = (
        envelope.get("stats", {}).get("models", {})
        if isinstance(envelope.get("stats"), dict)
        else {}
    )
    if isinstance(models, dict):
        for model_stats in models.values():
            if not isinstance(model_stats, dict):
                continue
            api = model_stats.get("api")
            if isinstance(api, dict) and isinstance(
                api.get("totalRequests"), int
            ):
                total += api["totalRequests"]
                found = True

    if not found:

        def _scan(node: object) -> None:
            nonlocal total, found
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "totalRequests" and isinstance(value, int):
                        total += value
                        found = True
                    else:
                        _scan(value)
            elif isinstance(node, list):
                for item in node:
                    _scan(item)

        _scan(envelope)

    return total if found and total > 0 else 1


def _scrubbed_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in _API_KEY_ENV_VARS:
        env.pop(key, None)
    return env


def _parse_envelope(stdout: str) -> dict:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        # Tolerate update notices / banner noise around the JSON.
        try:
            return json.loads(extract_json_object(stdout))
        except json.JSONDecodeError as exc:
            raise GeminiCliError(
                "Could not parse Gemini CLI JSON envelope from stdout: "
                f"{stdout[:500]!r}"
            ) from exc


def _classify_envelope_error(error: object) -> GeminiCliError:
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message") or json.dumps(error, ensure_ascii=False)
    else:
        code = None
        message = str(error)
    haystack = f"{code} {message}".lower()
    is_quota = (
        code == 429
        or "429" in str(code)
        or any(
            marker in haystack
            for marker in (
                "quota",
                "exhaust",
                "rate limit",
                "resource_exhausted",
            )
        )
    )
    if is_quota:
        return GeminiCliQuotaError(message)
    return GeminiCliError(message)


def _invoke_once(
    executable: str,
    model: str,
    prompt: str,
    cwd: Path | None,
    timeout: int,
) -> tuple[str, int, dict]:
    """One subprocess round-trip. Returns (response, requests, envelope)."""
    # --skip-trust avoids an interactive workspace-trust prompt that would
    # otherwise hang a non-interactive run (we own the staged workspace).
    cmd = [
        executable,
        "-m",
        model,
        "--output-format",
        "json",
        "--skip-trust",
        "--yolo",
    ]
    logger.debug(
        f"Running gemini CLI: argv={cmd} prompt_chars={len(prompt)} "
        f"(via stdin) cwd={cwd} timeout={timeout}s"
    )
    try:
        result = subprocess.run(
            cmd,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            capture_output=True,
            input=prompt,
            env=_scrubbed_env(),
            cwd=str(cwd) if cwd is not None else None,
        )
    except subprocess.TimeoutExpired as exc:
        raise GeminiCliError(f"gemini CLI timed out after {timeout}s") from exc

    stdout = result.stdout or ""
    if not stdout.strip():
        stderr_tail = "\n".join(
            (result.stderr or "").strip().splitlines()[-20:]
        )
        raise GeminiCliError(
            f"gemini CLI produced no stdout (exit {result.returncode}): "
            f"{stderr_tail}"
        )

    envelope = _parse_envelope(stdout)
    error = envelope.get("error")
    if error:
        raise _classify_envelope_error(error)

    response = envelope.get("response")
    if not isinstance(response, str) or not response.strip():
        if result.returncode != 0:
            stderr_tail = "\n".join(
                (result.stderr or "").strip().splitlines()[-20:]
            )
            raise GeminiCliError(
                f"gemini CLI exited {result.returncode} with no response: "
                f"{stderr_tail}"
            )
        raise GeminiCliError(
            "gemini CLI envelope had no usable 'response' field"
        )

    return response, extract_request_count(envelope), envelope


def run_gemini_cli(
    prompt: str,
    *,
    model: str,
    media_files: list[Path] | None = None,
    timeout: int | None = None,
) -> GeminiCliResult:
    """Invoke the Gemini CLI once and return the parsed result (single-shot).

    Prompt is fed via stdin (the full SRT is far too large for a Windows
    argv). ``media_files`` may be empty; when present, each file is staged
    into a private temp workspace and referenced by an ``@<relative-name>``
    include token with the subprocess ``cwd`` set to that workspace —
    gemini-cli's ``@`` parser resolves relative names, not absolute Windows
    paths.

    This is a pure text generator: it never enforces a response schema. Schema
    handling (the JSON-Schema prompt suffix + the validate-and-repair loop) is
    owned by ``run_inference`` and applied uniformly across every prompt-based
    backend, so the CLI backend has no schema-specific code path of its own.
    """
    media_files = media_files or []

    executable = shutil.which(_CLI_EXECUTABLE)
    if executable is None:
        raise GeminiCliNotInstalledError(
            f"Gemini CLI executable not found on PATH: {_CLI_EXECUTABLE!r}"
        )

    limit_bytes = _MAX_MEDIA_FILE_MB * 1024 * 1024
    for media in media_files:
        if not media.exists():
            raise GeminiCliError(f"Gemini CLI media file not found: {media}")
        size = media.stat().st_size
        if size > limit_bytes:
            raise GeminiCliError(
                f"Media file exceeds {_MAX_MEDIA_FILE_MB} MB CLI limit "
                f"({size / 1024 / 1024:.1f} MB): {media}"
            )

    effective_timeout = timeout or _CLI_TIMEOUT_SECS

    workspace: Path | None = None
    try:
        media_block = ""
        if media_files:
            workspace = Path(tempfile.mkdtemp(prefix="gemini_cli_"))
            tokens = []
            for index, src in enumerate(media_files):
                staged_name = f"{index:02d}_{src.name}"
                shutil.copy2(src, workspace / staged_name)
                tokens.append(f"@{staged_name}")
            media_block = "\n\n[ATTACHED MEDIA]\n" + "\n".join(tokens)

        response, requests, envelope = _invoke_once(
            executable, model, prompt + media_block, workspace, effective_timeout
        )
        stats = envelope.get("stats")
        stats = stats if isinstance(stats, dict) else {}
        logger.debug(
            f"gemini CLI ok: response_chars={len(response)} "
            f"requests={requests}"
        )
        return GeminiCliResult(
            response=response,
            requests=requests,
            stats=stats,
            raw_envelope=envelope,
        )
    finally:
        if workspace is not None:
            shutil.rmtree(workspace, ignore_errors=True)
