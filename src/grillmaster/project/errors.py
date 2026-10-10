"""Errors raised by the project store."""

from __future__ import annotations


class ProjectError(Exception):
    """Base class for project-store failures."""


class ProjectExistsError(ProjectError):
    """A project directory already holds a `project.json`."""


class ProjectNotFoundError(ProjectError):
    """No project directory (or no `project.json`) where one was expected."""
