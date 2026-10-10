from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from grillmaster.core.fs import staging_dir
from grillmaster.core.paths import (
    MAX_COMPONENT_UNITS,
    MAX_PATH_UNITS,
    attempt_path,
    measure,
)
from grillmaster.core.stage_key import SideTaskKey, StageKey
from grillmaster.project.layout import ProjectLayout, session_dir
from grillmaster.project.naming import (
    PROJECT_INNER_PATH_RESERVE,
    SAMPLE_ATTEMPT,
    SAMPLE_CHAT_BATCH,
    SAMPLE_CHUNK,
    SAMPLE_FRAME,
    SAMPLE_STAMP,
    archive_destination,
    archive_group,
    deliverable_name,
    deliverable_stem,
    inner_path_units,
    package_destination,
)
from grillmaster.project.state import DateResearchRecord, now

if TYPE_CHECKING:
    from grillmaster.project.state import ProjectState

LAYOUT = ProjectLayout(Path("projects/epabc123"))


def _every_layout_path() -> list[Path]:
    """Each `Path` property of the layout, plus its parameterized paths."""
    properties = [
        value
        for attribute in vars(ProjectLayout).values()
        if isinstance(attribute, property)
        and isinstance(value := attribute.fget(LAYOUT), Path)  # pyright: ignore[reportOptionalCall]
    ]
    batch_label = LAYOUT.chat_batch(SAMPLE_CHAT_BATCH).stem
    return [
        *properties,
        *(LAYOUT.work_dir(key) for key in StageKey),
        *(LAYOUT.side_dir(key) for key in SideTaskKey),
        *(frames / SAMPLE_FRAME for frames in LAYOUT.frames_dirs(SAMPLE_CHUNK)),
        *(
            attempt_path(session_dir(parent, label=label), SAMPLE_ATTEMPT) / name
            for parent in LAYOUT.session_parents(SAMPLE_CHUNK)
            for label in ("", "polish", batch_label)
            for name in ("prompt.md", "tools.json", "raw.jsonl", "result.json")
        ),
        LAYOUT.chunk_audio(*SAMPLE_CHUNK),
        LAYOUT.chunk_translation(*SAMPLE_CHUNK),
        LAYOUT.chat_batch(SAMPLE_CHAT_BATCH),
        LAYOUT.run_log(SAMPLE_STAMP),
        LAYOUT.events_log(SAMPLE_STAMP),
    ]


@pytest.mark.parametrize("path", _every_layout_path(), ids=str)
def test_every_layout_path_fits_the_reserve(path: Path):
    assert inner_path_units(LAYOUT, path) <= PROJECT_INNER_PATH_RESERVE


@pytest.mark.parametrize(
    ("broadcast_date", "name", "expected"),
    [
        (
            date(2026, 5, 3),
            "demo_show",
            ("260503_epabc123", "260503_epabc123_demo_show", "26/05"),
        ),
        (None, None, ("epabc123", "epabc123", "etc")),
        (None, "demo_show", ("epabc123", "epabc123_demo_show", "etc")),
    ],
    ids=["dated", "undated-unnamed", "undated"],
)
def test_deliverable_names(
    state: ProjectState,
    broadcast_date: date | None,
    name: str | None,
    expected: tuple[str, str, str],
):
    """`expected` is (stem, full name, archive group)."""
    state.broadcast_date = broadcast_date
    state.name = name
    group = archive_group(state).as_posix()
    assert (deliverable_stem(state), deliverable_name(state), group) == expected


def test_destinations(state: ProjectState, tmp_path: Path):
    state.broadcast_date = date(2026, 5, 3)
    state.name = "demo_show"
    assert archive_destination(state, tmp_path / "archive") == (
        tmp_path / "archive/26/05/260503_epabc123_demo_show"
    )
    assert package_destination(state, tmp_path / "package", reserve=24) == (
        tmp_path / "package/260503_epabc123_demo_show"
    )


def test_a_researched_date_names_an_undated_project(
    state: ProjectState, tmp_path: Path
):
    state.side_tasks.date_research = DateResearchRecord(
        completed_at=now(),
        elapsed_s=1.0,
        verdict="found",
        broadcast_date=date(2026, 5, 3),
    )
    assert deliverable_stem(state) == "260503_epabc123"
    assert archive_group(state) == Path("26") / "05"


def test_long_names_are_trimmed_to_the_budget(state: ProjectState, tmp_path: Path):
    state.broadcast_date = date(2026, 5, 3)
    state.name = "全力脱力タイムズ" * 40
    destination = archive_destination(state, tmp_path / "archive")

    assert destination.name.startswith("260503_epabc123_全力")
    assert measure(destination.name) <= MAX_COMPONENT_UNITS
    # The project is copied in under the staging name before the swap.
    staging = os.path.abspath(staging_dir(destination))  # noqa: PTH100
    assert measure(staging) + PROJECT_INNER_PATH_RESERVE <= MAX_PATH_UNITS


def test_long_package_names_leave_the_reserve_below_the_staging_name(
    state: ProjectState, tmp_path: Path
):
    state.broadcast_date = date(2026, 5, 3)
    state.name = "全力脱力タイムズ" * 40
    reserve = 30
    destination = package_destination(state, tmp_path / "package", reserve=reserve)

    staging = os.path.abspath(staging_dir(destination))  # noqa: PTH100
    assert destination.name.startswith("260503_epabc123_全力")
    assert measure(staging) + reserve <= MAX_PATH_UNITS
