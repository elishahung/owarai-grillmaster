"""Helpers shared by the commands: logging, failing, config and project lookup.

Commands import the heavy modules inside their functions, so `grill --help`
loads none of them.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import typer
from loguru import logger

from grillmaster.cli.args import looks_like_path
from grillmaster.events.context import install_log_context

if TYPE_CHECKING:
    from grillmaster.config.load import LoadedConfig
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState

_CONSOLE_FORMAT = (
    "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}"
)


def configure_console_logging() -> None:
    """INFO and up on stderr; the run log file keeps DEBUG (pipeline.logs)."""
    logger.remove()
    logger.add(sys.stderr, level="INFO", format=_CONSOLE_FORMAT)
    install_log_context()


def fail(message: str) -> NoReturn:
    typer.secho(f"Error: {message}", err=True, fg=typer.colors.RED)
    raise typer.Exit(code=1)


def load_or_exit() -> LoadedConfig:
    """`grill.toml` and `.env`, parsed and validated."""
    from grillmaster.config.errors import ConfigError
    from grillmaster.config.load import load_config

    try:
        return load_config()
    except ConfigError as error:
        fail(str(error))


def projects_root_or_exit() -> Path:
    """`projects/` under the working root; only locates `grill.toml`."""
    from grillmaster.config.errors import ConfigError
    from grillmaster.config.load import PROJECTS_DIR_NAME, find_config

    try:
        return find_config().parent / PROJECTS_DIR_NAME
    except ConfigError as error:
        fail(str(error))


def load_project_or_exit(text: str) -> tuple[ProjectLayout, ProjectState]:
    """A project named by ID/URL (under `projects/`) or by its directory.

    A directory path also reaches archived projects outside `projects/`, and
    needs no `grill.toml`.
    """
    from grillmaster.core.source_id import parse_source
    from grillmaster.project.errors import ProjectError
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.store import load_state

    if looks_like_path(text) and Path(text).is_dir():
        layout = ProjectLayout(Path(text))
    else:
        try:
            source = parse_source(text)
        except ValueError as error:
            fail(str(error))
        layout = ProjectLayout.for_id(projects_root_or_exit(), source.video_id)
    try:
        return layout, load_state(layout)
    except (ProjectError, ValueError) as error:
        fail(str(error))
