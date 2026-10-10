"""Translation failures."""

from __future__ import annotations


class TranslateError(Exception):
    """The translation could not produce its output (e.g. chunks failed)."""
