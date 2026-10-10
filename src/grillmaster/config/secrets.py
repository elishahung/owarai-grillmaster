"""API keys from `.env` (next to `grill.toml`) and the process environment.

`.env` holds secrets only; everything else lives in `grill.toml`. Other
variables in the file are ignored because a dotenv file is commonly shared
with unrelated tools. A missing key surfaces where it is needed (ASR), so
commands that never call ElevenLabs run without one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from pathlib import Path

ENV_FILE_NAME = ".env"


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8", extra="ignore", frozen=True
    )

    elevenlabs_api_key: SecretStr | None = None


def load_secrets(root: Path) -> Secrets:
    """Read `root/.env`; process environment variables take precedence."""
    return Secrets(_env_file=root / ENV_FILE_NAME)  # pyright: ignore[reportCallIssue]
