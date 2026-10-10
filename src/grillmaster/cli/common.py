"""Helpers shared by the commands: logging, failing, config and project lookup.

Commands import the heavy modules inside their functions, so `grill --help`
loads none of them.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import typer
from loguru import logger

from grillmaster.cli.args import looks_like_path
from grillmaster.events.context import install_log_context

if TYPE_CHECKING:
    from collections.abc import Iterator

    from grillmaster.config.load import LoadedConfig
    from grillmaster.pipeline.registry import Pipeline
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState
    from grillmaster.project.store import ProjectLocation

_CONSOLE_FORMAT = (
    "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}"
)

# The loguru handler id of the console sink, which the dashboard replaces
# while it owns the terminal.
_console_handler: int | None = None


def configure_console_logging() -> None:
    """INFO and up on stderr; the run log file keeps DEBUG (pipeline.logs)."""
    logger.remove()
    restore_console_logging()
    install_log_context()


def restore_console_logging() -> None:
    """Add the console sink (again), leaving every other handler alone."""
    global _console_handler  # noqa: PLW0603 - the one console sink of the process
    _console_handler = logger.add(sys.stderr, level="INFO", format=_CONSOLE_FORMAT)


def console_handler() -> int | None:
    """The console sink's loguru handler id, if one is installed."""
    return _console_handler


def pipeline_from(ctx: typer.Context) -> Pipeline:
    """The pipeline a command runs: the registry's, unless a test handed in
    its own as the context object."""
    from grillmaster.pipeline.registry import PIPELINE, Pipeline

    return ctx.obj if isinstance(ctx.obj, Pipeline) else PIPELINE


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


@contextmanager
def hold_project_or_exit(
    loaded: LoadedConfig, layout: ProjectLayout, project_id: str
) -> Iterator[ProjectState]:
    """Hold project `project_id`'s run lock (`project.store.project_lock`)
    for the block and yield its state, read again under the lock; a project
    another grill process holds, or one gone meanwhile, exits 1."""
    from grillmaster.project.errors import ProjectError
    from grillmaster.project.store import load_state, project_lock

    held = False
    try:
        with project_lock(loaded.projects_root, project_id):
            state = load_state(layout)
            held = True
            yield state
    except (ProjectError, ValueError) as error:
        if held:
            raise  # the block's own failure, not the hold's
        fail(str(error))


def locate_project_or_exit(
    text: str, loaded: LoadedConfig
) -> tuple[ProjectLocation, ProjectState]:
    """A project named by its directory (local when under `projects/`), or
    by ID/URL: its local project, else its archived one under `[paths]
    archive` (`project.store.locate_project`)."""
    from grillmaster.core.source_id import parse_source
    from grillmaster.project.errors import ProjectError
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.store import ProjectLocation, load_state, locate_project

    if looks_like_path(text) and Path(text).is_dir():
        layout = ProjectLayout(Path(text))
        local = layout.root.resolve().is_relative_to(loaded.projects_root.resolve())
        location = ProjectLocation(layout, local=local)
    else:
        try:
            source = parse_source(text)
        except ValueError as error:
            fail(str(error))
        try:
            found = locate_project(
                loaded.projects_root,
                source.video_id,
                archive_root=loaded.config.paths.archive,
            )
        except ProjectError as error:
            fail(str(error))
        if found is None:
            fail(f"No project for {source.video_id} (local or archived)")
        location = found
    try:
        return location, load_state(location.layout)
    except (ProjectError, ValueError) as error:
        fail(str(error))
