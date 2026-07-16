"""Shared contract for the unified model-inference layer.

One entry point — `run_inference` — drives every backend (Gemini API, Gemini
CLI, Codex CLI, Claude Agent SDK). A call is parameterized, not split into
modes:

  * `schema=None`  -> return the model's raw final message (the historical
    "agentic" behaviour; file-writing callers pass a `cwd` and inspect the
    files the agent wrote afterward).
  * `schema=<Model>` -> the JSON Schema is appended to the prompt and the
    output is validated-and-repaired until it parses, then returned as text.

Gemini backends additionally accept `audio`; the agent backends (Codex,
Claude) cannot ingest audio and raise `UnsupportedMediaError` if given any.
"""

from __future__ import annotations

import os
import signal
import subprocess
from enum import StrEnum

# Per-invocation timeout shared by every backend. A maintainer constant, not
# per-deployment configuration — the agent backends (gemini-cli / codex /
# claude) pass it straight to their subprocess/query timeout; gemini-api
# converts it to the genai SDK's milliseconds. 20 minutes covers the slowest
# high-effort chunk translations.
DEFAULT_TIMEOUT_SECS = 20 * 60


class Backend(StrEnum):
    """Selectable inference backend."""

    GEMINI_API = "gemini-api"
    GEMINI_CLI = "gemini-cli"
    GEMINI_AGY = "gemini-agy"
    CODEX = "codex"
    CLAUDE = "claude"


# gemini-agy (Antigravity CLI) is a Gemini agent backend like gemini-cli, but it
# cannot ingest audio — so it joins codex/claude as audio-incapable.
_AUDIO_CAPABLE = frozenset({Backend.GEMINI_API, Backend.GEMINI_CLI})
_GEMINI = frozenset(
    {Backend.GEMINI_API, Backend.GEMINI_CLI, Backend.GEMINI_AGY}
)
# Agent backends: subscription/OAuth, local, free. Everything EXCEPT the network
# gemini-api backend — gemini-cli and gemini-agy are agents too (local CLI
# subprocesses).
_AGENT = frozenset(
    {Backend.GEMINI_CLI, Backend.GEMINI_AGY, Backend.CODEX, Backend.CLAUDE}
)


def is_gemini_backend(backend: Backend) -> bool:
    """True for the Gemini backends (genai SDK, gemini CLI, or Antigravity CLI)."""
    return backend in _GEMINI


def is_agent_backend(backend: Backend) -> bool:
    """True for the agent backends (gemini-cli, gemini-agy, codex, claude).

    The api-vs-agent split is the core taxonomy: only gemini-api is an API
    (network HTTP, metered, fans out widely); the others are agents
    (local subscription processes, free, low concurrency).
    """
    return backend in _AGENT


def backend_supports_audio(backend: Backend) -> bool:
    """True when the backend can ingest audio attachments (Gemini only)."""
    return backend in _AUDIO_CAPABLE


def truncate_middle(text: str, *, head: int = 50, tail: int = 50) -> str:
    """Collapse a long string for logging: keep the head and tail, replace the
    middle with a count of omitted characters.

    Model final messages (raw SRT, JSON briefings) can be thousands of chars and
    flood the debug log. Strings short enough to fit in ``head + tail`` pass
    through unchanged.
    """
    text = text.rstrip()
    if len(text) <= head + tail:
        return text
    omitted = len(text) - head - tail
    return f"{text[:head]} ... [{omitted} chars omitted] ... {text[-tail:]}"


# Bound on the post-tree-kill pipe drain in `run_cli`. With every descendant
# dead the drain returns immediately; the bound only guards a failed kill.
_POST_KILL_DRAIN_SECS = 30


def kill_process_tree(process: subprocess.Popen) -> None:
    """Forcefully terminate a CLI process and every descendant.

    On Windows the agent CLIs resolve to batch shims (cmd.exe -> node), and an
    agent may spawn shell-tool children of its own. ``Popen.kill`` reaches
    only the direct child; a surviving descendant keeps the inherited
    stdout/stderr handles open, which would block a post-kill pipe drain
    indefinitely.
    """
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],
            check=False,
            capture_output=True,
        )
    else:
        # POSIX: run_cli starts the child in its own session, so its process
        # group id equals its pid and killpg reaps the whole tree.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()


def run_cli(
    cmd: list[str],
    *,
    input: str,
    timeout: int,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess:
    """``subprocess.run`` replacement that tree-kills the CLI on timeout.

    ``subprocess.run(timeout=...)`` kills only the direct child and then
    drains stdout/stderr with no time bound. When a node grandchild hangs
    (observed: a stalled model stream mid-turn), it keeps those pipes open
    forever, turning the timeout into a permanently wedged worker. Every
    CLI-shim backend (gemini-cli, codex) must go through this instead.
    """
    process = subprocess.Popen(
        cmd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=cwd,
        start_new_session=(os.name != "nt"),
    )
    try:
        stdout, stderr = process.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_process_tree(process)
        try:
            process.communicate(timeout=_POST_KILL_DRAIN_SECS)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass
        raise
    return subprocess.CompletedProcess(
        cmd, process.returncode, stdout, stderr
    )


class InferenceError(RuntimeError):
    """Base error for any backend invocation failure."""


class InferenceNotInstalledError(InferenceError):
    """Raised when a backend's executable or runtime is unavailable."""


class UnsupportedMediaError(InferenceError):
    """Raised when media is passed to a backend that cannot ingest it."""
