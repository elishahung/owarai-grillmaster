"""Source failures."""

from __future__ import annotations


class SourceError(Exception):
    """A source could not provide what the pipeline asked for."""


class SourceHttpError(SourceError):
    """A platform HTTP API request failed or returned something not JSON."""
