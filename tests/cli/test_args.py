from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from grillmaster.cli.args import (
    DEFAULT_POOL,
    PICK_PARENT_FLAG,
    expand_bare_options,
    looks_like_path,
    parse_section_time,
    reject_directory_source,
    resolve_remix,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        pytest.param(["ep1", "--remix"], ["ep1", "--remix", DEFAULT_POOL], id="last"),
        pytest.param(
            ["ep1", "--remix", "--chat"],
            ["ep1", "--remix", DEFAULT_POOL, "--chat"],
            id="before-option",
        ),
        pytest.param(
            ["ep1", "--remix", "rain"], ["ep1", "--remix", "rain"], id="value"
        ),
        pytest.param(["ep1", "--remix=rain"], ["ep1", "--remix=rain"], id="equals"),
        pytest.param(["ep1", "--chat"], ["ep1", "--chat"], id="absent"),
    ],
)
def test_expand_bare_options(args: list[str], expected: list[str]):
    assert expand_bare_options(args) == expected


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        pytest.param(["ep1", "--parent"], ["ep1", PICK_PARENT_FLAG], id="last"),
        pytest.param(
            ["--parent", "ep1", "hint"], [PICK_PARENT_FLAG, "ep1", "hint"], id="source"
        ),
        pytest.param(
            ["ep1", "--parent", "--chat"],
            ["ep1", PICK_PARENT_FLAG, "--chat"],
            id="before-option",
        ),
        pytest.param(
            ["ep1", "--parent", "nas/ep0"],
            ["ep1", "--parent", "nas/ep0"],
            id="path-value",
        ),
        pytest.param(
            ["ep1", "--parent=nas/ep0"], ["ep1", "--parent=nas/ep0"], id="equals"
        ),
    ],
)
def test_expand_bare_parent(args: list[str], expected: list[str]):
    assert expand_bare_options(args) == expected


def test_a_directory_after_parent_is_its_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "ep0").mkdir()
    monkeypatch.chdir(tmp_path)
    assert expand_bare_options(["--parent", "ep0", "ep1"]) == [
        "--parent",
        "ep0",
        "ep1",
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), (DEFAULT_POOL, "noise"), ("rain", "rain")],
)
def test_resolve_remix(value: str | None, expected: str | None):
    assert resolve_remix(value, "noise") == expected


def test_directory_source_is_refused(tmp_path: Path):
    project = tmp_path / "epabc123"
    project.mkdir()
    with pytest.raises(ValueError, match="grill package"):
        reject_directory_source(str(project))


@pytest.mark.parametrize(
    "text", ["epabc123", "https://tver.jp/episodes/epabc123", "v=dQw4w9WgXcQ"]
)
def test_ids_and_urls_are_never_paths(text: str):
    assert not looks_like_path(text)
    reject_directory_source(text)


@pytest.mark.parametrize("text", ["projects/epabc123", r"V:\show\epabc123"])
def test_slashes_make_a_path(text: str):
    assert looks_like_path(text)


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("90", 90.0), ("1:30", 90.0), ("0:01:30", 90.0), ("1h30m", 5400.0)],
)
def test_parse_section_time(text: str, seconds: float):
    assert parse_section_time(text) == seconds


@pytest.mark.parametrize("text", ["soon", "-5"])
def test_parse_section_time_rejects_garbage(text: str):
    with pytest.raises(ValueError, match="Invalid time"):
        parse_section_time(text)
