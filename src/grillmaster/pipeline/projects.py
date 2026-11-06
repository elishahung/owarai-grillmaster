"""Find and open the project a run works on: the local project of the
source, created on first sight; an archived ID (or a local one that is also
archived) is refused instead."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.stage_key import StageKey
from grillmaster.project.errors import ArchivedProjectError
from grillmaster.project.store import (
    create_project,
    load_state,
    locate_project,
    resolve_parent,
    save_state,
)

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState
    from grillmaster.stages.base import RunOptions

type OpenedProject = tuple[ProjectLayout, ProjectState]


def find_project(
    projects_root: Path, options: RunOptions, *, archive_root: Path | None
) -> OpenedProject | None:
    """The local project of `options.source`, `None` before its first run.

    A source already archived under `archive_root` raises
    `ArchivedProjectError` (naming the `grill package` command): stages
    never re-run on an archived project, and starting it over locally would
    create a second copy. A local project with an archived copy of the same
    ID raises `DuplicateProjectError` before any stage runs (the archive
    move would refuse it at the end); see `project.store.locate_project`.
    """
    location = locate_project(
        projects_root, options.source.video_id, archive_root=archive_root
    )
    if location is None:
        return None
    if not location.local:
        raise ArchivedProjectError(options.source.video_id, location.layout.root)
    return location.layout, load_state(location.layout)


def open_project(
    projects_root: Path, options: RunOptions, existing: OpenedProject | None
) -> OpenedProject:
    """`existing` (from `find_project`), or a new project for `options.source`.

    On an existing project a hint and a parent apply only while the pre-pass
    has not run (both are pre-pass inputs). Afterwards a different hint is
    ignored and a different parent refused, since it would not seed anything;
    repeating the same parent is fine (a resumed serial run does).
    """
    if existing is None:
        return create_project(
            projects_root,
            options.source,
            translation_hint=options.hint,
            parent=options.parent,
        )
    layout, state = existing
    logger.info(f"Loaded existing project {state.id} ({state.name or 'unnamed'})")
    if options.parent is not None and options.parent.resolve() != state.parent:
        if state.is_done(StageKey.PREPASS):
            raise ValueError(
                f"--parent {options.parent} differs from the parent {state.id}'s "
                f"pre-pass read ({state.parent}); run `grill reset {state.id} "
                "--from prepass` to change it"
            )
        state.parent = resolve_parent(options.parent)
        save_state(layout, state)
        logger.info(f"Parent set for the existing project: {state.parent}")
    if options.hint is not None and options.hint != state.translation_hint:
        if state.is_done(StageKey.PREPASS):
            logger.warning(
                "Translation hint ignored: the pre-pass already ran "
                f"(grill reset {state.id} --from prepass to apply it)"
            )
        else:
            state.translation_hint = options.hint
            save_state(layout, state)
            logger.info("Translation hint updated for the existing project")
    return layout, state
