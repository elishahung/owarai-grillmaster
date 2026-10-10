from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from grillmaster.package.errors import PoolError
from grillmaster.package.inserts import INSERT_OUTPUT_MAX_LENGTH, Insert, copy_inserts
from grillmaster.package.pools import CURSOR_FILE_NAME

if TYPE_CHECKING:
    from pathlib import Path


def make_pool(package_root: Path, name: str, contents: list[str]) -> Path:
    directory = package_root / "pools" / name
    directory.mkdir(parents=True)
    for index, content in enumerate(contents, start=1):
        (directory / f"{index:03d}.mp4").write_text(content, encoding="utf-8")
    return directory


def test_each_insert_copies_its_pools_next_file(tmp_path: Path):
    package_root = tmp_path / "package"
    target = tmp_path / "target"
    target.mkdir()
    judge = make_pool(package_root, "judge", ["first", "second"])
    (judge / CURSOR_FILE_NAME).write_text('{"index": 1}', encoding="utf-8")
    make_pool(package_root, "ending", ["credits"])

    copies = copy_inserts(
        [Insert(pool="judge", output="judge"), Insert(pool="ending", output="outro")],
        package_root=package_root,
        target_dir=target,
    )

    assert copies == [target / "judge.mp4", target / "outro.mp4"]
    assert (target / "judge.mp4").read_text(encoding="utf-8") == "second"
    assert (target / "outro.mp4").read_text(encoding="utf-8") == "credits"
    cursor = json.loads((judge / CURSOR_FILE_NAME).read_text(encoding="utf-8"))
    assert cursor["index"] == 0


def test_no_inserts_copies_nothing(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    assert copy_inserts([], package_root=tmp_path / "package", target_dir=target) == []
    assert list(target.iterdir()) == []


def test_a_declared_pool_that_is_missing_fails(tmp_path: Path):
    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(PoolError, match="not found"):
        copy_inserts(
            [Insert(pool="judge", output="judge")],
            package_root=tmp_path / "package",
            target_dir=target,
        )


def test_an_overlong_suffix_fails_before_any_cursor_moves(tmp_path: Path):
    package_root = tmp_path / "package"
    target = tmp_path / "target"
    target.mkdir()
    judge = make_pool(package_root, "judge", ["first"])
    ending = package_root / "pools" / "ending"
    ending.mkdir(parents=True)
    (ending / "001.matroska").write_text("credits", encoding="utf-8")

    with pytest.raises(PoolError, match=r"001\.matroska"):
        copy_inserts(
            [Insert(pool="judge", output="judge"), Insert(pool="ending", output="end")],
            package_root=package_root,
            target_dir=target,
        )
    assert not (judge / CURSOR_FILE_NAME).exists()
    assert list(target.iterdir()) == []


def test_insert_outputs_are_capped_for_the_path_reserve():
    Insert(pool="judge", output="x" * INSERT_OUTPUT_MAX_LENGTH)
    with pytest.raises(ValueError, match="longer than"):
        Insert(pool="judge", output="x" * (INSERT_OUTPUT_MAX_LENGTH + 1))
