"""Shared contract for the unified model-inference layer.

One entry point — `run_inference` — drives every backend (Antigravity CLI,
Codex CLI, Claude Agent SDK). All three are local subscription agents. A
call is parameterized, not split into modes:

  * `schema=None`  -> return the model's raw final message (the historical
    "agentic" behaviour; file-writing callers pass a `cwd` and inspect the
    files the agent wrote afterward).
  * `schema=<Model>` -> the JSON Schema is appended to the prompt and the
    output is validated-and-repaired until it parses, then returned as text.

agy additionally accepts `audio`; Codex and Claude cannot ingest audio and
raise `UnsupportedMediaError` if given any.
"""

from __future__ import annotations

import os
import signal
import subprocess
from enum import StrEnum

from grillmaster.legacy.settings import settings


def default_timeout_secs() -> int:
    """Per-invocation timeout shared by every backend, from AGENT_TIMEOUT_MINUTES.

    Every backend passes it straight to its subprocess/query timeout.
    Resolved per call rather than captured in a module constant, so the configured value is honoured wherever a backend
    falls back to the default.
    """
    return settings.agent_timeout_minutes * 60


class Backend(StrEnum):
    """Selectable inference backend."""

    AGY = "agy"
    CODEX = "codex"
    CLAUDE = "claude"


# Gemini (agy, through its view_file tool) hears audio; codex / claude cannot.
_AUDIO_CAPABLE = frozenset({Backend.AGY})


def backend_supports_audio(backend: Backend) -> bool:
    """True when the backend can ingest audio attachments (agy only)."""
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
    CLI-shim backend (codex) must go through this instead.
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
    return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)


class InferenceError(RuntimeError):
    """Base error for any backend invocation failure."""


class InferenceNotInstalledError(InferenceError):
    """Raised when a backend's executable or runtime is unavailable."""


class InferenceQuotaError(InferenceError):
    """Raised when a subscription backend reports its quota / rate limit."""


class UnsupportedMediaError(InferenceError):
    """Raised when media is passed to a backend that cannot ingest it."""
