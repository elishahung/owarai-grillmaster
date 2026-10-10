from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import date
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from grillmaster.core.source_id import Platform, SourceId
from grillmaster.core.stage_key import StageKey
from grillmaster.project.errors import ProjectExistsError, ProjectNotFoundError
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.store import (
    archive_project,
    create_project,
    load_state,
    save_state,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from grillmaster.project.state import ProjectState

SOURCE = SourceId(Platform.BILIBILI, "BV1ZArvBaEqL")


def test_create_persists_the_initial_state(tmp_path: Path):
    layout, state = create_project(tmp_path, SOURCE, translation_hint="漫才")

    assert layout.root == tmp_path / "BV1ZArvBaEqL"
    assert layout.project_json.is_file()
    assert load_state(layout) == state
    assert state.translation_hint == "漫才"


def test_create_refuses_an_existing_project(tmp_path: Path):
    create_project(tmp_path, SOURCE)
    with pytest.raises(ProjectExistsError):
        create_project(tmp_path, SOURCE)


def test_parent_must_be_a_project(tmp_path: Path):
    (tmp_path / "not_a_project").mkdir()
    with pytest.raises(ProjectNotFoundError, match="Parent"):
        create_project(tmp_path / "projects", SOURCE, parent=tmp_path / "not_a_project")
    assert not ProjectLayout.for_id(
        tmp_path / "projects", SOURCE.video_id
    ).root.exists()


def test_parent_is_stored_resolved(layout: ProjectLayout, tmp_path: Path):
    _, child = create_project(
        tmp_path / "other", SOURCE, parent=layout.root / "x" / ".."
    )
    assert child.parent == layout.root.resolve()


def test_save_then_load_round_trips(layout: ProjectLayout, state: ProjectState):
    state.name = "全力脱力タイムズ"
    state.mark_done(StageKey.METADATA, elapsed_s=2.0, params={"official_cc": "on"})
    save_state(layout, state)

    assert load_state(layout) == state
    assert "全力脱力タイムズ" in layout.project_json.read_text(encoding="utf-8")
    assert [path.name for path in layout.root.iterdir()] == ["project.json"]


def test_load_missing_project_raises(tmp_path: Path):
    with pytest.raises(ProjectNotFoundError):
        load_state(ProjectLayout(tmp_path / "missing"))


def test_load_invalid_project_raises(layout: ProjectLayout):
    layout.project_json.write_text(
        '{"id": "x", "is_asr_completed": true}', encoding="utf-8"
    )
    with pytest.raises(ValidationError):
        load_state(layout)


def test_archive_moves_dated_projects_under_year_month(
    layout: ProjectLayout, state: ProjectState, tmp_path: Path
):
    state.broadcast_date = date(2026, 5, 3)
    state.name = "demo_show"
    layout.ja_srt.parent.mkdir()
    layout.ja_srt.write_text("1\n", encoding="utf-8")

    archived = archive_project(layout, state, tmp_path / "archive")

    assert archived.root == tmp_path / "archive/26/05/260503_epabc123_demo_show"
    assert archived.ja_srt.read_text(encoding="utf-8") == "1\n"
    assert not layout.root.exists()


def test_archive_puts_undated_projects_under_etc(
    layout: ProjectLayout, state: ProjectState, tmp_path: Path
):
    archived = archive_project(layout, state, tmp_path / "archive")
    assert archived.root == tmp_path / "archive/etc/epabc123"


def test_archive_replaces_only_the_leaf(
    layout: ProjectLayout, state: ProjectState, tmp_path: Path
):
    stale = tmp_path / "archive/etc/epabc123"
    sibling = tmp_path / "archive/etc/other"
    (stale / "old.txt").parent.mkdir(parents=True)
    (stale / "old.txt").write_text("old", encoding="utf-8")
    sibling.mkdir()

    archived = archive_project(layout, state, tmp_path / "archive")

    assert archived.root == stale
    assert not (stale / "old.txt").exists()
    assert archived.project_json.is_file()
    assert sibling.is_dir()


def test_archive_requires_the_project_directory(state: ProjectState, tmp_path: Path):
    with pytest.raises(ProjectNotFoundError):
        archive_project(ProjectLayout(tmp_path / "gone"), state, tmp_path / "archive")


def test_archive_of_an_archived_project_is_a_no_op(
    layout: ProjectLayout, state: ProjectState, tmp_path: Path
):
    archived = archive_project(layout, state, tmp_path / "archive")
    again = archive_project(archived, state, tmp_path / "archive")
    assert again.root == archived.root
    assert again.project_json.is_file()


def test_archive_replacement_leaves_no_backup(
    layout: ProjectLayout, state: ProjectState, tmp_path: Path
):
    stale = tmp_path / "archive/etc/epabc123"
    stale.mkdir(parents=True)
    archive_project(layout, state, tmp_path / "archive")
    assert sorted(path.name for path in stale.parent.iterdir()) == ["epabc123"]


@contextmanager
def locked(path: Path) -> Iterator[None]:
    """Make `path` undeletable while the block runs: an open handle on
    Windows, a read-only parent directory on POSIX."""
    if sys.platform == "win32":
        with path.open("rb"):
            yield
        return
    parent = path.parent
    mode = parent.stat().st_mode
    parent.chmod(0o500)
    try:
        yield
    finally:
        parent.chmod(mode)


def test_a_locked_source_keeps_the_complete_archive(
    layout: ProjectLayout, state: ProjectState, tmp_path: Path
):
    layout.ja_srt.parent.mkdir()
    layout.ja_srt.write_text("1\n", encoding="utf-8")

    with locked(layout.ja_srt):
        archived = archive_project(layout, state, tmp_path / "archive")

    # The copy was verified before the swap, so the failed cleanup of the
    # local project neither raises nor rolls the archive back.
    assert archived.root == tmp_path / "archive/etc/epabc123"
    assert archived.ja_srt.read_text(encoding="utf-8") == "1\n"
    assert load_state(archived) == state
    assert layout.ja_srt.exists()
    assert sorted(path.name for path in archived.root.parent.iterdir()) == ["epabc123"]


def test_a_stale_staging_copy_is_replaced(
    layout: ProjectLayout, state: ProjectState, tmp_path: Path
):
    stale = tmp_path / "archive/etc/epabc123.partial"
    stale.mkdir(parents=True)
    (stale / "half_copied.mp4").write_bytes(b"crash")

    archived = archive_project(layout, state, tmp_path / "archive")

    assert not (archived.root / "half_copied.mp4").exists()
    assert not stale.exists()
    assert not layout.root.exists()
