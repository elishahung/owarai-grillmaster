"""API keys from `.env` (next to `grill.toml`) and the process environment.

`.env` holds secrets only; everything else lives in `grill.toml`. Other
variables in the file are ignored because a dotenv file is commonly shared
with unrelated tools. A missing key surfaces where it is needed (the ASR
and proofread preflights), so commands that never call ElevenLabs, Meta or
Azure run without them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from grillmaster.config.errors import ConfigError
from grillmaster.config.load import ENV_FILE_NAME
from grillmaster.core.asr_provider import AsrProvider

if TYPE_CHECKING:
    from pathlib import Path


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8", extra="ignore", frozen=True
    )

    # The speech providers' (`[asr] provider`, `[watch_clip] speech`). The
    # Azure endpoint is no secret but is tied to the key's Azure resource.
    elevenlabs_api_key: SecretStr | None = None
    azure_speech_key: SecretStr | None = None
    azure_speech_endpoint: SecretStr | None = None
    # `watch_clip`'s Muse key. The tool server reads its keys again from
    # `.env` alone; required here so a missing key fails before the run.
    meta_api_key: SecretStr | None = None

    def require_elevenlabs_api_key(self) -> str:
        """The ElevenLabs key; `ConfigError` when it is not set."""
        return _require(self.elevenlabs_api_key, "ELEVENLABS_API_KEY")

    def require_azure_speech(self) -> tuple[str, str]:
        """The Azure Speech endpoint and key; `ConfigError` when one is not set."""
        return (
            _require(self.azure_speech_endpoint, "AZURE_SPEECH_ENDPOINT"),
            _require(self.azure_speech_key, "AZURE_SPEECH_KEY"),
        )

    def require_speech_keys(self, provider: AsrProvider) -> None:
        """The keys `provider` needs; `ConfigError` when one is not set."""
        match provider:
            case AsrProvider.MAI:
                self.require_azure_speech()
            case AsrProvider.SCRIBE:
                self.require_elevenlabs_api_key()

    def require_watch_clip_keys(self, speech: AsrProvider | None) -> None:
        """`watch_clip`'s Meta key and its speech provider's keys;
        `ConfigError` when one is not set."""
        _require(self.meta_api_key, "META_API_KEY")
        if speech is not None:
            self.require_speech_keys(speech)


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
