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
    CODEX = "codex"
    CLAUDE = "claude"


_AUDIO_CAPABLE = frozenset({Backend.GEMINI_API, Backend.GEMINI_CLI})
_GEMINI = frozenset({Backend.GEMINI_API, Backend.GEMINI_CLI})
# Agent backends: subscription/OAuth, local, free. Everything EXCEPT the network
# gemini-api backend — gemini-cli is an agent too (a local CLI subprocess).
_AGENT = frozenset({Backend.GEMINI_CLI, Backend.CODEX, Backend.CLAUDE})


def is_gemini_backend(backend: Backend) -> bool:
    """True for the Gemini backends (genai SDK or gemini CLI)."""
    return backend in _GEMINI


def is_agent_backend(backend: Backend) -> bool:
    """True for the agent backends (gemini-cli, codex, claude).

    The api-vs-agent split is the core taxonomy: only gemini-api is an API
    (network HTTP, metered, fans out widely); the other three are agents
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


class InferenceError(RuntimeError):
    """Base error for any backend invocation failure."""


class InferenceNotInstalledError(InferenceError):
    """Raised when a backend's executable or runtime is unavailable."""


class UnsupportedMediaError(InferenceError):
    """Raised when media is passed to a backend that cannot ingest it."""
