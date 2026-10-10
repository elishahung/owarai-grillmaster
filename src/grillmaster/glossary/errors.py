"""Errors raised while loading the fixed glossary."""

from __future__ import annotations


class GlossaryError(Exception):
    """The glossary file is missing, not JSON, or not glossary-shaped."""
