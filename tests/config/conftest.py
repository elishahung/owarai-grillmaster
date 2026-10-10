from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.fixture
def roles() -> dict[str, str]:
    return {
        "prepass": "agy/gemini-3.1-pro/high",
        "chunk": "agy/gemini-3.1-pro",
        "postprocess": "codex/gpt-5.6-sol/medium",
        "utility": "codex/gpt-5.5/medium",
        "image": "codex/gpt-5.5/high",
    }


@pytest.fixture
def roles_toml(roles: dict[str, str]) -> str:
    """`[agents.roles]` text equal to the `roles` fixture."""
    lines = [f'{role} = "{spec}"' for role, spec in roles.items()]
    return "\n".join(["[agents.roles]", *lines]) + "\n"


@pytest.fixture
def minimal_data(roles: dict[str, str]) -> dict[str, Any]:
    """The smallest valid `grill.toml` content: only the required roles."""
    return {"agents": {"roles": roles}}


@pytest.fixture
def write_toml() -> Callable[[Path, str], Path]:
    """Write `grill.toml` text byte-for-byte into a directory."""

    def write(directory: Path, text: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "grill.toml"
        path.write_bytes(text.encode("utf-8"))
        return path

    return write


@pytest.fixture(autouse=True)
def _no_real_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's own ElevenLabs key out of config tests."""
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
