from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from grillmaster.core.source_id import Platform, SourceId
from grillmaster.core.stage_key import StageKey
from grillmaster.pipeline.projects import open_project
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.store import create_project, load_state, save_state
from grillmaster.stages.base import RunOptions

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.config.load import LoadedConfig
    from grillmaster.project.state import ProjectState

SOURCE = SourceId(Platform.TVER, "epnew1")


def test_creates_a_new_project(loaded: LoadedConfig):
    layout, state = open_project(
        loaded.projects_root, RunOptions(source=SOURCE, hint="漫才")
    )
    assert layout == ProjectLayout.for_id(loaded.projects_root, "epnew1")
    assert load_state(layout) == state
    assert state.translation_hint == "漫才"


def test_loads_an_existing_project(
    loaded: LoadedConfig, layout: ProjectLayout, state: ProjectState
):
    opened_layout, opened = open_project(
        loaded.projects_root, RunOptions(source=state.source_id)
    )
    assert opened_layout == layout
    assert opened == state


def test_hint_applies_before_the_prepass(
    loaded: LoadedConfig, layout: ProjectLayout, state: ProjectState
):
    _, opened = open_project(
        loaded.projects_root, RunOptions(source=state.source_id, hint="新提示")
    )
    assert opened.translation_hint == "新提示"
    assert load_state(layout).translation_hint == "新提示"


def test_hint_is_ignored_after_the_prepass(
    loaded: LoadedConfig, layout: ProjectLayout, state: ProjectState
):
    state.mark_done(StageKey.PREPASS, elapsed_s=1.0)
    save_state(layout, state)
    _, opened = open_project(
        loaded.projects_root, RunOptions(source=state.source_id, hint="太晚了")
    )
    assert opened.translation_hint is None


def test_parent_is_fixed_at_creation(loaded: LoadedConfig, tmp_path: Path):
    parent_layout, _ = create_project(
        tmp_path / "archive", SourceId(Platform.TVER, "epold")
    )
    options = RunOptions(source=SOURCE, parent=parent_layout.root)
    _, created = open_project(loaded.projects_root, options)
    assert created.parent == parent_layout.root.resolve()
    # A resumed serial run repeats the same parent.
    open_project(loaded.projects_root, options)
    with pytest.raises(ValueError, match="fixed at creation"):
        open_project(loaded.projects_root, replace(options, parent=tmp_path))
