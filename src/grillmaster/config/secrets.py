"""API keys from `.env` (next to `grill.toml`) and the process environment.

`.env` holds secrets only; everything else lives in `grill.toml`. Other
variables in the file are ignored because a dotenv file is commonly shared
with unrelated tools. A missing key surfaces where it is needed (ASR), so
commands that never call ElevenLabs or Meta run without them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from grillmaster.config.errors import ConfigError
from grillmaster.config.load import ENV_FILE_NAME

if TYPE_CHECKING:
    from pathlib import Path


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8", extra="ignore", frozen=True
    )

    elevenlabs_api_key: SecretStr | None = None
    # Read again by the tool server (`agent_tools.meta`) from `.env` alone;
    # required here so a missing key fails before the run starts.
    meta_api_key: SecretStr | None = None

    def require_elevenlabs_api_key(self) -> str:
        """The ElevenLabs key; `ConfigError` when it is not set."""
        return _require(self.elevenlabs_api_key, "ELEVENLABS_API_KEY")

    def require_meta_api_key(self) -> str:
        """The Meta key (`watch_clip`); `ConfigError` when it is not set."""
        return _require(self.meta_api_key, "META_API_KEY")


def _require(key: SecretStr | None, name: str) -> str:
    if key is None or not key.get_secret_value():
        raise ConfigError(
            f"{name} is not set; add it to the {ENV_FILE_NAME} "
            "beside grill.toml or to the environment"
        )
    return key.get_secret_value()


def load_secrets(root: Path) -> Secrets:
    """Read `root/.env`; process environment variables take precedence."""
    return Secrets(_env_file=root / ENV_FILE_NAME)  # pyright: ignore[reportCallIssue] - a pydantic-settings init option
