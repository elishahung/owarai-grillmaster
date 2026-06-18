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

    async def _collect() -> str:
        final_message = ""
        async for message in query(prompt=prompt_arg, options=options):
            if isinstance(message, AssistantMessage):
                text = "".join(
                    block.text
                    for block in message.content
                    if isinstance(block, TextBlock)
                )
                if text:
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
        raise ClaudeSDKExecError(
            f"claude-agent-sdk query failed: {exc}"
        ) from exc

    if output_last_message_path is not None:
        capture_path = output_last_message_path.resolve()
        capture_path.parent.mkdir(parents=True, exist_ok=True)
        capture_path.write_text(final_message, encoding="utf-8")

    if final_message.strip():
        logger.debug(f"Claude SDK final message:\n{final_message.rstrip()}")
    return final_message


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
