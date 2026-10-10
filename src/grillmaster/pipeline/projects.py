"""Open the project a run works on: load it when it exists, else create it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.stage_key import StageKey
from grillmaster.project.errors import ProjectNotFoundError
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.store import create_project, load_state, save_state

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.pipeline.stage import RunOptions
    from grillmaster.project.state import ProjectState


def open_project(
    projects_root: Path, options: RunOptions
) -> tuple[ProjectLayout, ProjectState]:
    """The project for `options.source`, created on first sight.

    On an existing project a hint applies only while the pre-pass has not
    run (it is a pre-pass input). The parent seeds the pre-pass and is fixed
    at creation: repeating it is fine (a resumed serial run does), a
    different one is refused.
    """
    layout = ProjectLayout.for_id(projects_root, options.source.video_id)
    try:
        state = load_state(layout)
    except ProjectNotFoundError:
        return create_project(
            projects_root,
            options.source,
            translation_hint=options.hint,
            parent=options.parent,
        )
    logger.info(f"Loaded existing project {state.id} ({state.name or 'unnamed'})")
    if options.parent is not None and options.parent.resolve() != state.parent:
        raise ValueError(
            f"--parent {options.parent} differs from the parent {state.id} was "
            f"created with ({state.parent}); the parent is fixed at creation"
        )
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
