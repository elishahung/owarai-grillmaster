from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import date
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from grillmaster.core.source_id import Platform, SourceId
from grillmaster.core.stage_key import StageKey
from grillmaster.project.errors import (
    DuplicateProjectError,
    ProjectBusyError,
    ProjectExistsError,
    ProjectNotFoundError,
)
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.state import ProjectState
from grillmaster.project.store import (
    LOCKS_DIR_NAME,
    ProjectLocation,
    archive_project,
    create_project,
    find_archived,
    load_state,
    locate_project,
    project_lock,
    save_state,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


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


def test_an_archived_copy_under_another_date_blocks_a_second_one(
    layout: ProjectLayout, state: ProjectState, tmp_path: Path
):
    # Archived once with one broadcast date; the local copy now has another.
    first = state.model_copy(update={"broadcast_date": date(2026, 5, 3)})
    complete = ProjectLayout(tmp_path / "archive/26/05/260503_epabc123")
    save_state(complete, first)
    state.broadcast_date = date(2026, 6, 1)

    with pytest.raises(ProjectExistsError, match="260503_epabc123"):
        archive_project(layout, state, tmp_path / "archive")

    assert not (tmp_path / "archive/26/06").exists()
    assert layout.project_json.is_file()


def test_an_archived_project_moves_within_the_archive_after_a_date_change(
    layout: ProjectLayout, state: ProjectState, tmp_path: Path
):
    archived = archive_project(layout, state, tmp_path / "archive")
    state.broadcast_date = date(2026, 6, 1)

    moved = archive_project(archived, state, tmp_path / "archive")

    assert moved.root == tmp_path / "archive/26/06/260601_epabc123"
    assert load_state(moved).id == state.id
    assert not archived.root.exists()


def test_locate_project_prefers_local_then_archived(
    layout: ProjectLayout, state: ProjectState, tmp_path: Path
):
    projects, archive = layout.root.parent, tmp_path / "archive"
    local = ProjectLocation(layout, local=True)
    assert locate_project(projects, state.id, archive_root=archive) == local
    assert locate_project(projects, state.id, archive_root=None) == local

    archived = archive_project(layout, state, archive)
    assert locate_project(projects, state.id, archive_root=archive) == (
        ProjectLocation(archived, local=False)
    )
    assert locate_project(projects, state.id, archive_root=None) is None
    assert locate_project(projects, "epother1", archive_root=archive) is None


def test_locate_project_refuses_a_local_project_that_is_also_archived(
    layout: ProjectLayout, state: ProjectState, tmp_path: Path
):
    archive = tmp_path / "archive"
    archived = ProjectLayout(tmp_path / "archive/26/05/260503_epabc123")
    save_state(archived, state)

    with pytest.raises(DuplicateProjectError) as caught:
        locate_project(layout.root.parent, state.id, archive_root=archive)
    assert str(layout.root) in str(caught.value)
    assert str(archived.root) in str(caught.value)


def test_an_unlistable_archive_is_logged_and_skipped_but_archiving_fails(
    layout: ProjectLayout, state: ProjectState, tmp_path: Path, warnings: list[str]
):
    # A file stands in for an archive that cannot be listed (an unreachable
    # NAS): scanning it raises an OSError other than FileNotFoundError.
    unreachable = tmp_path / "archive"
    unreachable.write_text("", encoding="utf-8")
    projects = layout.root.parent

    found = locate_project(projects, state.id, archive_root=unreachable)
    missing = locate_project(projects, "epother1", archive_root=unreachable)

    assert found == ProjectLocation(layout, local=True)
    assert missing is None
    assert len(warnings) == 2
    assert f"cannot check {unreachable} for an archived copy" in warnings[0]
    with pytest.raises(OSError):  # noqa: PT011 - any listing failure
        archive_project(layout, state, unreachable)
    assert layout.project_json.is_file()


def test_a_missing_archive_root_holds_no_copy(
    layout: ProjectLayout, state: ProjectState, tmp_path: Path, warnings: list[str]
):
    archive = tmp_path / "not-yet"
    assert locate_project(layout.root.parent, state.id, archive_root=archive) == (
        ProjectLocation(layout, local=True)
    )
    assert warnings == []


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
    layout: ProjectLayout, state: ProjectState, tmp_path: Path, warnings: list[str]
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
    assert sorted(path.name for path in archived.root.parent.iterdir()) == ["epabc123"]
    # project.json went first: the leftover is no project a re-run could
    # resume and archive over the complete copy.
    assert layout.ja_srt.exists()
    assert not layout.project_json.exists()
    assert any(str(layout.root) in warning for warning in warnings)


def test_archive_refuses_a_destination_holding_a_project(
    layout: ProjectLayout, state: ProjectState, tmp_path: Path
):
    complete = ProjectLayout(tmp_path / "archive/etc/epabc123")
    save_state(complete, state)
    (complete.root / "video.mp4").write_bytes(b"complete")

    with pytest.raises(ProjectExistsError, match="manually"):
        archive_project(layout, state, tmp_path / "archive")

    assert (complete.root / "video.mp4").read_bytes() == b"complete"
    assert layout.project_json.is_file()


@pytest.mark.parametrize(
    ("aired", "name", "relative"),
    [
        pytest.param(
            date(2026, 5, 3), "demo", "26/05/260503_epabc123_demo", id="dated"
        ),
        pytest.param(date(2026, 5, 3), None, "26/05/260503_epabc123", id="unnamed"),
        pytest.param(None, "demo", "etc/epabc123_demo", id="undated"),
    ],
)
def test_find_archived_locates_the_project_by_id(
    *,
    layout: ProjectLayout,
    state: ProjectState,
    tmp_path: Path,
    aired: date | None,
    name: str | None,
    relative: str,
):
    state.broadcast_date = aired
    state.name = name
    archived = archive_project(layout, state, tmp_path / "archive")

    assert archived.root == tmp_path / "archive" / relative
    assert find_archived(tmp_path / "archive", "epabc123") == archived
    assert find_archived(tmp_path / "archive", "epother1") is None
    assert find_archived(tmp_path / "missing", "epabc123") is None


def test_find_archived_confirms_the_id_and_skips_staging(
    state: ProjectState, tmp_path: Path
):
    archive_root = tmp_path / "archive"
    # `ep1_x` is another project whose name pattern also fits ID `ep1`.
    longer = ProjectState.create(SourceId(Platform.YOUTUBE, "ep1_x"))
    save_state(ProjectLayout(archive_root / "etc/ep1_x"), longer)
    save_state(ProjectLayout(archive_root / "etc/epabc123.partial"), state)
    save_state(ProjectLayout(archive_root / "etc/epabc123.old"), state)

    assert find_archived(archive_root, "ep1") is None
    assert find_archived(archive_root, "epabc123") is None
    assert find_archived(archive_root, "ep1_x") == ProjectLayout(
        archive_root / "etc/ep1_x"
    )


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


# --- project_lock ----------------------------------------------------------------


def test_a_second_holder_of_a_project_fails_at_once(tmp_path: Path):
    projects = tmp_path / "projects"
    with project_lock(projects, "epabc123"):
        with (
            pytest.raises(
                ProjectBusyError,
                match=r"^project epabc123 is in use by another grill process$",
            ),
            project_lock(projects, "epabc123"),
        ):
            pass
        with project_lock(projects, "epother1"):  # other projects are free
            pass
    with project_lock(projects, "epabc123"):  # released on exit
        pass


def test_a_failed_body_releases_the_project(tmp_path: Path):
    projects = tmp_path / "projects"
    with pytest.raises(RuntimeError), project_lock(projects, "epabc123"):
        raise RuntimeError("stage broke")
    with project_lock(projects, "epabc123"):
        pass


def test_a_timeout_in_the_body_is_not_a_busy_project(tmp_path: Path):
    with pytest.raises(TimeoutError, match="ffmpeg"), project_lock(tmp_path, "ep1"):
        raise TimeoutError("ffmpeg timed out")


def test_a_locked_project_archives_without_its_lock(
    layout: ProjectLayout, state: ProjectState, tmp_path: Path
):
    # The run holds the lock across the archive move (Windows cannot move a
    # directory holding an open file) and the packaging after it.
    projects = layout.root.parent
    with project_lock(projects, state.id):
        archived = archive_project(layout, state, tmp_path / "archive")
        with pytest.raises(ProjectBusyError), project_lock(projects, state.id):
            pass

    assert not layout.root.exists()
    assert sorted(path.name for path in archived.root.iterdir()) == ["project.json"]
    assert (projects / LOCKS_DIR_NAME / f"{state.id}.lock").is_file()
