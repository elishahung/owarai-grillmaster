"""Claude Agent SDK backend — a drop-in peer of `run_codex_exec`.

Maps the Codex CLI contract onto `claude_agent_sdk.query`:

    codex exec --cd CWD              -> ClaudeAgentOptions(cwd=CWD)
    codex exec --yolo                -> permission_mode="bypassPermissions"
    codex exec -m MODEL              -> ClaudeAgentOptions(model=...)
    prompt via stdin                 -> query(prompt=...)
    --image PATH (repeatable)        -> base64 image blocks in a user message
    --output-last-message FILE       -> capture the final AssistantMessage text
    subprocess timeout               -> asyncio.wait_for(...)

Auth: the SDK's bundled CLI uses the local Claude Code subscription login;
no `ANTHROPIC_API_KEY` is required (or wanted — that would bill per-token).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

from .base import AgentExecError, AgentNotInstalledError


class ClaudeSDKExecError(AgentExecError):
    """Raised when the Claude Agent SDK query fails or times out."""


class ClaudeSDKRateLimitError(ClaudeSDKExecError):
    """Raised when the subscription session / rate limit is hit (HTTP 429).

    The bundled CLI reports this mid-stream as a ``RateLimitEvent`` plus a 429
    ``ResultMessage``, then exits non-zero — which the SDK otherwise surfaces as
    the opaque ``"returned an error result: success"``. We detect the 429 and
    raise this with the CLI's own human-readable reset message instead. Peer of
    `GeminiCliQuotaError`.
    """


class ClaudeSDKNotInstalledError(AgentNotInstalledError):
    """Raised when the `claude-agent-sdk` package is not importable."""


_IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# Default per-invocation timeout for an SDK query. Hardcoded maintainer constant.
_DEFAULT_TIMEOUT_SECS = 900
# Claude model / reasoning effort used when a caller does not pass them.
_DEFAULT_MODEL = "claude-opus-4-8"
_DEFAULT_REASONING_EFFORT = "high"


def run_claude_sdk_exec(
    prompt: str,
    cwd: Path,
    images: list[Path] | None = None,
    output_last_message_path: Path | None = None,
    timeout: int | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> str:
    """Invoke the Claude Agent SDK once and return the final assistant message.

    Synchronous facade over an async query so existing call sites stay sync.
    Safe because refine/glossary run on the main thread with no live event
    loop; `asyncio.run` would raise if one were already running.
    """
    try:
        from claude_agent_sdk import (  # noqa: PLC0415 — optional dependency
            AssistantMessage,
            ClaudeAgentOptions,
            RateLimitEvent,
            ResultMessage,
            TextBlock,
            query,
        )
    except ImportError as exc:
        raise ClaudeSDKNotInstalledError(
            "claude-agent-sdk is not installed; run `uv add claude-agent-sdk` "
            "or switch AGENT_POSTPROCESS_BACKEND back to 'codex'"
        ) from exc

    abs_cwd = cwd.resolve()
    effective_timeout = timeout or _DEFAULT_TIMEOUT_SECS
    effective_model = model or _DEFAULT_MODEL
    effective_effort = (reasoning_effort or _DEFAULT_REASONING_EFFORT).lower()

    options = ClaudeAgentOptions(
        cwd=str(abs_cwd),
        model=effective_model,
        effort=effective_effort,
        permission_mode="bypassPermissions",
    )

    # codex feeds the prompt via stdin; with images we must instead stream a
    # structured user message carrying base64 image blocks alongside the text.
    if images:
        prompt_arg = _image_prompt(prompt, images)
    else:
        prompt_arg = prompt

    logger.debug(
        f"Running claude-agent-sdk query: cwd={abs_cwd} "
        f"model={effective_model} prompt_chars={len(prompt)} "
        f"images={len(images or [])} timeout={effective_timeout}s"
    )

    # Populated mid-stream if the CLI reports a 429. Checked after the loop so
    # the limit is surfaced as ClaudeSDKRateLimitError instead of the opaque
    # "returned an error result: success" the SDK raises on the trailing exit.
    rate_limit: dict[str, str] = {}

    async def _collect() -> str:
        final_message = ""
        async for message in query(prompt=prompt_arg, options=options):
            if isinstance(message, RateLimitEvent):
                info = message.rate_limit_info
                if info is not None and info.status == "rejected":
                    rate_limit.setdefault("text", _rate_limit_text(info))
            elif isinstance(message, ResultMessage):
                if (
                    message.is_error
                    and getattr(message, "api_error_status", None) == 429
                    and message.result
                ):
                    rate_limit["text"] = message.result
            elif isinstance(message, AssistantMessage):
                text = "".join(
                    block.text
                    for block in message.content
                    if isinstance(block, TextBlock)
                )
                # A rate-limit AssistantMessage carries the human "session limit"
                # notice as its text; keep it out of the real final message.
                if getattr(message, "error", None) == "rate_limit":
                    if text:
                        rate_limit["text"] = text
                elif text:
                    final_message = text
        return final_message

    async def _run() -> str:
        try:
            return await asyncio.wait_for(_collect(), timeout=effective_timeout)
        except asyncio.TimeoutError as exc:
            raise ClaudeSDKExecError(
                f"claude-agent-sdk query timed out after {effective_timeout}s"
            ) from exc

    try:
        final_message = asyncio.run(_run())
    except ClaudeSDKExecError:
        raise
    except Exception as exc:  # noqa: BLE001 — surface any SDK failure uniformly
        # A 429 makes the CLI exit non-zero, so the stream raises here; prefer the
        # captured limit message over the opaque "error result: success".
        if rate_limit:
            raise ClaudeSDKRateLimitError(
                f"Claude rate limit hit: {rate_limit['text']}"
            ) from exc
        raise ClaudeSDKExecError(
            f"claude-agent-sdk query failed: {exc}"
        ) from exc

    # Defensive: should a CLI version end the stream cleanly on a 429 rather than
    # exiting non-zero, surface the limit instead of an empty final message.
    if rate_limit:
        raise ClaudeSDKRateLimitError(f"Claude rate limit hit: {rate_limit['text']}")

    if output_last_message_path is not None:
        capture_path = output_last_message_path.resolve()
        capture_path.parent.mkdir(parents=True, exist_ok=True)
        capture_path.write_text(final_message, encoding="utf-8")

    # The final message is logged centrally by `run_inference` (one site for
    # every backend, with middle-truncation), not here.
    return final_message


def _rate_limit_text(info) -> str:
    """Human-readable fallback when no CLI-supplied notice is available.

    The CLI usually emits a friendly "session limit … resets <time>" message we
    prefer; this reconstructs an equivalent from the raw `RateLimitInfo` fields.
    """
    from datetime import datetime  # noqa: PLC0415 — local, mirrors module style

    parts = [f"{info.rate_limit_type or 'rate'} limit reached"]
    if info.resets_at:
        when = datetime.fromtimestamp(info.resets_at).strftime("%Y-%m-%d %H:%M:%S")
        parts.append(f"resets at {when}")
    return "; ".join(parts)


async def _image_prompt(prompt: str, images: list[Path]):
    """Yield a single user message: the text plus one block per image."""
    import base64

    content: list[dict] = [{"type": "text", "text": prompt}]
    for img in images:
        media_type = _IMAGE_MEDIA_TYPES.get(img.suffix.lower())
        if media_type is None:
            raise ClaudeSDKExecError(f"unsupported image type: {img}")
        data = base64.standard_b64encode(img.resolve().read_bytes()).decode()
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": data,
                },
            }
        )
    yield {"type": "user", "message": {"role": "user", "content": content}}
