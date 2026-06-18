"""Shared contract for provider-agnostic agent executors.

Every backend (Codex CLI, Claude Agent SDK, ...) exposes the same callable:

    run_<provider>_exec(
        prompt: str,
        cwd: Path,
        images: list[Path] | None = None,
        output_last_message_path: Path | None = None,
        timeout: int | None = None,
    ) -> str

i.e. "run an agent with full file-tool access inside `cwd`, feed it `prompt`
(plus optional images), and return its final assistant message". The agent
does its real work by reading/writing files in `cwd`; callers validate those
files afterwards.
"""

from __future__ import annotations

from enum import StrEnum


class AgentBackend(StrEnum):
    """Selectable agent executor backend."""

    CODEX = "codex"
    CLAUDE = "claude"


class AgentExecError(RuntimeError):
    """Base error for any agent backend invocation failure."""


class AgentNotInstalledError(AgentExecError):
    """Raised when a backend's executable or runtime is unavailable."""
