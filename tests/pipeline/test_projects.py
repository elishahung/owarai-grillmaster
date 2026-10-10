from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from grillmaster.core.source_id import Platform, SourceId
from grillmaster.core.stage_key import StageKey
from grillmaster.pipeline.projects import find_project, open_project
from grillmaster.project.errors import ArchivedProjectError, DuplicateProjectError
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.store import (
    archive_project,
    create_project,
    load_state,
    save_state,
)
from grillmaster.stages.base import RunOptions

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.config.load import LoadedConfig
    from grillmaster.project.state import ProjectState

SOURCE = SourceId(Platform.TVER, "epnew1")


def _open(
    loaded: LoadedConfig, options: RunOptions, *, archive_root: Path | None = None
) -> tuple[ProjectLayout, ProjectState]:
    root = loaded.projects_root
    return open_project(
        root, options, find_project(root, options, archive_root=archive_root)
    )


def test_creates_a_new_project(loaded: LoadedConfig):
    layout, state = _open(loaded, RunOptions(source=SOURCE, hint="漫才"))
    assert layout == ProjectLayout.for_id(loaded.projects_root, "epnew1")
    assert load_state(layout) == state
    assert state.translation_hint == "漫才"


def test_loads_an_existing_project(
    loaded: LoadedConfig, layout: ProjectLayout, state: ProjectState
):
    opened_layout, opened = _open(loaded, RunOptions(source=state.source_id))
    assert opened_layout == layout
    assert opened == state


def test_hint_applies_before_the_prepass(
    loaded: LoadedConfig, layout: ProjectLayout, state: ProjectState
):
    _, opened = _open(loaded, RunOptions(source=state.source_id, hint="新提示"))
    assert opened.translation_hint == "新提示"
    assert load_state(layout).translation_hint == "新提示"


def test_hint_is_ignored_after_the_prepass(
    loaded: LoadedConfig, layout: ProjectLayout, state: ProjectState
):
    state.mark_done(StageKey.PREPASS, elapsed_s=1.0)
    save_state(layout, state)
    _, opened = _open(loaded, RunOptions(source=state.source_id, hint="太晚了"))
    assert opened.translation_hint is None


def test_parent_is_fixed_at_creation(loaded: LoadedConfig, tmp_path: Path):
    parent_layout, _ = create_project(
        tmp_path / "archive", SourceId(Platform.TVER, "epold")
    )
    options = RunOptions(source=SOURCE, parent=parent_layout.root)
    _, created = _open(loaded, options)
    assert created.parent == parent_layout.root.resolve()
    # A resumed serial run repeats the same parent.
    _open(loaded, options)
    with pytest.raises(ValueError, match="fixed at creation"):
        _open(loaded, replace(options, parent=tmp_path))


def test_a_cleanup_leftover_does_not_hide_the_archived_project(
    loaded: LoadedConfig, layout: ProjectLayout, state: ProjectState, tmp_path: Path
):
    archive_root = tmp_path / "archive"
    archived = archive_project(layout, state, archive_root)
    # What a failed local cleanup leaves: files, but no project.json.
    layout.ja_srt.parent.mkdir(parents=True)
    layout.ja_srt.write_text("leftover", encoding="utf-8")

    with pytest.raises(ArchivedProjectError, match="already archived") as caught:
        _open(loaded, RunOptions(source=state.source_id), archive_root=archive_root)
    assert f'grill package "{archived.root}"' in str(caught.value)
    assert not layout.project_json.exists()


def test_a_local_project_also_archived_is_refused_before_any_stage(
    loaded: LoadedConfig, layout: ProjectLayout, state: ProjectState, tmp_path: Path
):
    archive_root = tmp_path / "archive"
    archived = ProjectLayout(archive_root / "etc" / state.id)
    save_state(archived, state)

    with pytest.raises(DuplicateProjectError, match="remove the stale one") as caught:
        find_project(
            loaded.projects_root,
            RunOptions(source=state.source_id),
            archive_root=archive_root,
        )
    assert (caught.value.local, caught.value.archived) == (layout.root, archived.root)
