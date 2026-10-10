from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.config.secrets import load_secrets

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_reads_the_env_file_next_to_grill_toml(tmp_path: Path):
    (tmp_path / ".env").write_text(
        "ELEVENLABS_API_KEY=from-file\nUNRELATED_TOOL_SETTING=1\n", encoding="utf-8"
    )
    secrets = load_secrets(tmp_path)
    assert secrets.elevenlabs_api_key is not None
    assert secrets.elevenlabs_api_key.get_secret_value() == "from-file"


def test_process_environment_wins_over_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / ".env").write_text("ELEVENLABS_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "from-env")
    secrets = load_secrets(tmp_path)
    assert secrets.elevenlabs_api_key is not None
    assert secrets.elevenlabs_api_key.get_secret_value() == "from-env"


def test_missing_key_is_none_and_never_printed(tmp_path: Path):
    assert load_secrets(tmp_path).elevenlabs_api_key is None
    (tmp_path / ".env").write_text("ELEVENLABS_API_KEY=hush\n", encoding="utf-8")
    assert "hush" not in repr(load_secrets(tmp_path))
