from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from tests.package.conftest import PackageFfmpeg

from grillmaster.package.errors import PoolError
from grillmaster.package.pools import CURSOR_FILE_NAME, MediaPool, require_pools

if TYPE_CHECKING:
    from pathlib import Path


def make_pool(tmp_path: Path, names: list[str], cursor: dict | None = None) -> Path:
    directory = tmp_path / "pools" / "sleep"
    directory.mkdir(parents=True)
    for name in names:
        (directory / name).write_text(name, encoding="utf-8")
    if cursor is not None:
        (directory / CURSOR_FILE_NAME).write_text(json.dumps(cursor), encoding="utf-8")
    return directory


def cursor_of(directory: Path) -> dict:
    return json.loads((directory / CURSOR_FILE_NAME).read_text(encoding="utf-8"))


def probing(directory: Path, durations: dict[str, float]) -> PackageFfmpeg:
    return PackageFfmpeg({directory / name: value for name, value in durations.items()})


def cuts_of(pool: MediaPool, ffmpeg: PackageFfmpeg, count: int):
    return [
        (cut.source.name, cut.start, cut.duration)
        for cut in pool.reserve_seconds(60, count=count, ffmpeg=ffmpeg)
    ]


def test_under_names_the_pool_in_the_package_root(tmp_path: Path):
    pool = MediaPool.under(tmp_path, "judge")
    assert pool.directory == tmp_path / "pools" / "judge"
    assert pool.name == "judge"


# --- reserve_seconds (ported noise cursor) --------------------------------------


def test_reserve_advances_within_one_file(tmp_path: Path):
    directory = make_pool(
        tmp_path, ["001.mp4", "002.mp4"], cursor={"index": 0, "seconds": 120}
    )
    ffmpeg = probing(directory, {"001.mp4": 600.0, "002.mp4": 600.0})

    assert cuts_of(MediaPool(directory), ffmpeg, 2) == [
        ("001.mp4", 120.0, 60.0),
        ("001.mp4", 180.0, 60.0),
    ]
    assert cursor_of(directory) == {"index": 0, "seconds": 240}


def test_reserve_keeps_an_exactly_full_remainder(tmp_path: Path):
    directory = make_pool(tmp_path, ["001.mp4"])
    ffmpeg = probing(directory, {"001.mp4": 240.0})

    cuts = cuts_of(MediaPool(directory), ffmpeg, 3)

    assert [duration for _, _, duration in cuts] == [60.0, 60.0, 60.0]
    assert cursor_of(directory) == {"index": 0, "seconds": 180}


def test_reserve_swallows_a_short_remainder(tmp_path: Path):
    directory = make_pool(
        tmp_path, ["001.webm", "002.mp4"], cursor={"index": 0, "seconds": 120}
    )
    ffmpeg = probing(directory, {"001.webm": 220.0, "002.mp4": 600.0})

    assert cuts_of(MediaPool(directory), ffmpeg, 2) == [
        ("001.webm", 120.0, 100.0),
        ("002.mp4", 0.0, 60.0),
    ]
    assert cursor_of(directory) == {"index": 1, "seconds": 60}


def test_reserve_wraps_back_to_the_first_file(tmp_path: Path):
    directory = make_pool(tmp_path, ["001.mp4", "002.webm"])
    ffmpeg = probing(directory, {"001.mp4": 100.0, "002.webm": 100.0})

    assert cuts_of(MediaPool(directory), ffmpeg, 2) == [
        ("001.mp4", 0.0, 100.0),
        ("002.webm", 0.0, 100.0),
    ]
    assert cursor_of(directory) == {"index": 0, "seconds": 0}


def test_reserve_orders_mixed_extensions_by_index(tmp_path: Path):
    directory = make_pool(tmp_path, ["002.mp4", "003.mkv", "001.webm"])
    ffmpeg = probing(directory, {"001.webm": 60.0, "002.mp4": 60.0, "003.mkv": 60.0})

    cuts = cuts_of(MediaPool(directory), ffmpeg, 3)

    assert [name for name, _, _ in cuts] == ["001.webm", "002.mp4", "003.mkv"]
    assert cursor_of(directory) == {"index": 0, "seconds": 0}


def test_reserve_skips_a_cursor_past_the_end_of_a_file(tmp_path: Path):
    directory = make_pool(
        tmp_path, ["001.mp4", "002.mp4"], cursor={"index": 0, "seconds": 500}
    )
    ffmpeg = probing(directory, {"001.mp4": 300.0, "002.mp4": 300.0})

    assert cuts_of(MediaPool(directory), ffmpeg, 1) == [("002.mp4", 0.0, 60.0)]


def test_reserve_rejects_bad_arguments(tmp_path: Path):
    pool = MediaPool(make_pool(tmp_path, ["001.mp4"]))
    with pytest.raises(ValueError, match="count"):
        pool.reserve_seconds(60, count=0, ffmpeg=PackageFfmpeg())
    with pytest.raises(ValueError, match="seconds"):
        pool.reserve_seconds(0, count=1, ffmpeg=PackageFfmpeg())


def test_reserve_fails_on_a_pool_of_empty_media(tmp_path: Path):
    directory = make_pool(tmp_path, ["001.mp4"])
    with pytest.raises(PoolError, match="No usable media"):
        cuts_of(MediaPool(directory), probing(directory, {"001.mp4": 0.0}), 1)


# --- next_file (ported placeholder rotation) ------------------------------------


def test_next_file_takes_the_cursor_position_and_advances(tmp_path: Path):
    names = [f"{index:03d}.mp4" for index in range(1, 13)]
    directory = make_pool(tmp_path, names, cursor={"index": 9})

    assert MediaPool(directory).next_file() == directory / "010.mp4"
    assert cursor_of(directory) == {"index": 10, "seconds": 0}


def test_next_file_wraps_after_the_last_file(tmp_path: Path):
    directory = make_pool(tmp_path, ["001.mp4", "002.mp4", "003.mp4"], {"index": 2})

    assert MediaPool(directory).next_file().name == "003.mp4"
    assert cursor_of(directory) == {"index": 0, "seconds": 0}


def test_next_file_restarts_when_the_cursor_overruns(tmp_path: Path):
    directory = make_pool(tmp_path, ["001.mp4", "002.mp4", "003.mp4"], {"index": 96})

    assert MediaPool(directory).next_file().name == "001.mp4"
    assert cursor_of(directory) == {"index": 1, "seconds": 0}


def test_next_file_starts_at_the_first_without_a_cursor(tmp_path: Path):
    directory = make_pool(tmp_path, ["001.mp4", "002.mp4"])

    assert MediaPool(directory).next_file().name == "001.mp4"
    assert cursor_of(directory) == {"index": 1, "seconds": 0}


# --- pool shape -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("names", "message"),
    [
        pytest.param([], "empty", id="empty"),
        pytest.param(["001.mp4", "002.mp4", "004.mp4"], "without gaps", id="gap"),
        pytest.param(["000.mp4", "001.mp4"], "without gaps", id="zero-based"),
        pytest.param(["notes.txt"], "empty", id="unnumbered-only"),
    ],
)
def test_misnumbered_pools_are_rejected(tmp_path: Path, names: list[str], message: str):
    with pytest.raises(PoolError, match=message):
        MediaPool(make_pool(tmp_path, names)).next_file()


def test_missing_pool_is_an_error(tmp_path: Path):
    with pytest.raises(PoolError, match="not found"):
        MediaPool(tmp_path / "pools" / "nope").next_file()


def test_unreadable_cursor_is_an_error(tmp_path: Path):
    directory = make_pool(tmp_path, ["001.mp4"])
    (directory / CURSOR_FILE_NAME).write_text("{oops", encoding="utf-8")
    with pytest.raises(PoolError, match="Invalid pool cursor"):
        MediaPool(directory).next_file()


def test_require_pools_checks_every_named_pool(tmp_path: Path):
    make_pool(tmp_path, ["001.mp4"])
    require_pools(tmp_path, ["sleep"])
    with pytest.raises(PoolError, match="judge"):
        require_pools(tmp_path, ["sleep", "judge"])


def test_require_pools_moves_no_cursor(tmp_path: Path):
    directory = make_pool(tmp_path, ["001.mp4", "002.mp4"])
    require_pools(tmp_path, ["sleep"])
    assert not (directory / CURSOR_FILE_NAME).exists()
