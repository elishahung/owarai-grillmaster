"""Errors raised while finding, parsing or updating `grill.toml`."""

from __future__ import annotations


class ConfigError(Exception):
    """`grill.toml` or `.env` is missing, malformed or inconsistent."""


class ConfigNotFoundError(ConfigError):
    """No `grill.toml` above the working directory or in `$GRILL_HOME`."""
