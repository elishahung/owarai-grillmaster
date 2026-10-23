"""API keys for the tools that call metered services, read from `.env` alone.

The manifest names the `.env` path, never a key: gemini's CLI strips every
variable named like a key from its MCP servers, so a key only in the
environment would work for some callers and not others. `dotenv` is
imported on use.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def read_env_values(
    env_file: Path, names: tuple[str, ...], error: type[Exception]
) -> dict[str, str]:
    """`names` from `env_file` alone; `error` names every one not set."""
    from dotenv import dotenv_values  # noqa: PLC0415 - imported on use

    values = dotenv_values(env_file, encoding="utf-8")
    missing = [name for name in names if not values.get(name)]
    if missing:
        raise error(f"{', '.join(missing)} not set in {env_file}")
    return {name: str(values[name]) for name in names}
