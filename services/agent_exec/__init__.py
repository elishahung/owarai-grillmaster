"""Provider-agnostic agent executors.

`run_agent_exec` dispatches to a concrete backend (Codex CLI or Claude Agent
SDK) that all share one contract: run an agent with full file-tool access in a
cwd, feed it a prompt (+ optional images), return the final assistant message.
"""

from __future__ import annotations

from pathlib import Path

from .base import (
    AgentBackend,
    AgentExecError,
    AgentNotInstalledError,
)
from .codex import (
    CodexInvocationError,
    CodexNotInstalledError,
    run_codex_exec,
)
from .claude_sdk import (
    ClaudeSDKExecError,
    ClaudeSDKNotInstalledError,
    run_claude_sdk_exec,
)

__all__ = [
    "AgentBackend",
    "AgentExecError",
    "AgentNotInstalledError",
    "CodexInvocationError",
    "CodexNotInstalledError",
    "ClaudeSDKExecError",
    "ClaudeSDKNotInstalledError",
    "run_agent_exec",
    "run_codex_exec",
    "run_claude_sdk_exec",
]


def run_agent_exec(
    prompt: str,
    cwd: Path,
    *,
    backend: AgentBackend,
    images: list[Path] | None = None,
    output_last_message_path: Path | None = None,
    timeout: int | None = None,
) -> str:
    """Run `prompt` through the selected agent backend, returning its final message."""
    if backend == AgentBackend.CODEX:
        return run_codex_exec(
            prompt=prompt,
            cwd=cwd,
            images=images,
            output_last_message_path=output_last_message_path,
            timeout=timeout,
        )
    if backend == AgentBackend.CLAUDE:
        return run_claude_sdk_exec(
            prompt=prompt,
            cwd=cwd,
            images=images,
            output_last_message_path=output_last_message_path,
            timeout=timeout,
        )
    raise AgentExecError(f"unknown agent backend: {backend!r}")
