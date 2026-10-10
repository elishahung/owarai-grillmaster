"""Backend adapters, loaded on first use.

`claude_agent_sdk` is heavy to import, so each adapter module is imported
only when its backend is first looked up; a run that never touches Claude
never pays for it.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from grillmaster.core.model_spec import Backend

if TYPE_CHECKING:
    from grillmaster.agents.adapters.base import AgentAdapter


def load_adapter(backend: Backend) -> AgentAdapter:
    """A fresh adapter for `backend` with its real CLI wiring."""
    match backend:
        case Backend.AGY:
            from grillmaster.agents.adapters.agy import AgyAdapter

            return AgyAdapter()
        case Backend.GEMINI:
            from grillmaster.agents.adapters.gemini import GeminiAdapter

            return GeminiAdapter()
        case Backend.CODEX:
            from grillmaster.agents.adapters.codex import CodexAdapter

            return CodexAdapter()
        case Backend.CLAUDE:
            from grillmaster.agents.adapters.claude import ClaudeAdapter

            return ClaudeAdapter()


class AdapterRegistry:
    """The runner's adapter lookup: each backend's adapter is created (and
    cached) on its first call."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loaded: dict[Backend, AgentAdapter] = {}

    def __call__(self, backend: Backend) -> AgentAdapter:
        with self._lock:
            if backend not in self._loaded:
                self._loaded[backend] = load_adapter(backend)
            return self._loaded[backend]
