"""Find and load `grill.toml` plus its sibling `.env`.

The first `grill.toml` found walking up from the working directory wins,
then `$GRILL_HOME/grill.toml`. Its directory is the root: `projects/`, `.env`
and relative paths in the file all hang off it, so `grill` runs from anywhere
inside (or, with `GRILL_HOME`, outside) that tree.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from grillmaster.config.errors import ConfigError, ConfigNotFoundError
from grillmaster.config.model import validate_config
from grillmaster.config.secrets import load_secrets

if TYPE_CHECKING:
    from collections.abc import Mapping

    from grillmaster.config.model import AppConfig
    from grillmaster.config.secrets import Secrets

CONFIG_FILE_NAME = "grill.toml"
HOME_ENV_VAR = "GRILL_HOME"


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    root: Path
    config: AppConfig
    secrets: Secrets

    @property
    def path(self) -> Path:
        return self.root / CONFIG_FILE_NAME


def find_config(
    start: Path | None = None, env: Mapping[str, str] | None = None
) -> Path:
    """Locate `grill.toml` from `start` (default: cwd) upward, then `$GRILL_HOME`."""
    start = (start or Path.cwd()).resolve()
    env = os.environ if env is None else env
    for directory in (start, *start.parents):
        candidate = directory / CONFIG_FILE_NAME
        if candidate.is_file():
            return candidate
    searched = f"{start} and its parents"
    if home := env.get(HOME_ENV_VAR):
        candidate = Path(home) / CONFIG_FILE_NAME
        if candidate.is_file():
            return candidate.resolve()
        searched += f", and {candidate}"
    else:
        searched += f" ({HOME_ENV_VAR} is not set)"
    raise ConfigNotFoundError(
        f"No {CONFIG_FILE_NAME} found in {searched}. Copy grill.example.toml "
        f"to {CONFIG_FILE_NAME} in your working root, or set {HOME_ENV_VAR} "
        "to the directory holding it."
    )


def load_config(
    start: Path | None = None, env: Mapping[str, str] | None = None
) -> LoadedConfig:
    """Find, parse and validate `grill.toml`, and read the sibling `.env`."""
    path = find_config(start, env)
    return LoadedConfig(
        root=path.parent, config=read_config(path), secrets=load_secrets(path.parent)
    )


def read_config(path: Path) -> AppConfig:
    """Parse and validate one `grill.toml`, raising `ConfigError` with its path."""
    try:
        # utf-8-sig: Windows editors may prepend a BOM to a hand-edited file.
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path} is not valid TOML: {error}") from error
    try:
        return validate_config(data, root=path.parent)
    except ValidationError as error:
        raise ConfigError(f"{path} is invalid:\n{error}") from error
