"""Errors raised by the post-processing passes."""

from __future__ import annotations


class PostprocessError(Exception):
    """A post-processing input is missing or unusable."""
