from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


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
