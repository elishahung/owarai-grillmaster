"""Errors raised while packaging a deliverable."""

from __future__ import annotations


class PackageError(Exception):
    """Packaging cannot produce a complete deliverable."""


class RenderAbortedError(PackageError):
    """A render job did not start because another job of its render failed."""


class PoolError(PackageError):
    """A media pool is missing, misnumbered, or has an unreadable cursor."""
