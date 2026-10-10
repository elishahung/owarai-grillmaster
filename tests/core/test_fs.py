from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import pytest

from grillmaster.core.fs import (
    STAGING_SUFFIX,
    atomic_write_text,
    exclusive_lock,
    staged_dir,
    staging_dir,
)


def test_atomic_write_creates_parents_and_leaves_no_temp_files(tmp_path: Path):
    path = tmp_path / "a" / "b" / "out.txt"
    atomic_write_text(path, "first")
    atomic_write_text(path, "second\n")
    assert path.read_bytes() == b"second\n"
    assert [p.name for p in path.parent.iterdir()] == ["out.txt"]


def test_atomic_write_failure_keeps_old_content_and_cleans_up(tmp_path: Path):
    path = tmp_path / "out.txt"
    atomic_write_text(path, "old")
    with pytest.raises(TypeError):
        atomic_write_text(path, 123)  # pyright: ignore[reportArgumentType]
    assert path.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["out.txt"]


def test_atomic_write_retries_a_transient_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    target = tmp_path / "state.json"
    real_replace = Path.replace
    failures = iter([PermissionError("locked")])

    def flaky_replace(self: Path, other: Path) -> Path:
        if (error := next(failures, None)) is not None:
            raise error
        return real_replace(self, other)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    atomic_write_text(target, "ok")
    assert target.read_text(encoding="utf-8") == "ok"


# --- staged_dir ---------------------------------------------------------------


def test_staged_dir_builds_under_the_staging_name_then_swaps(tmp_path: Path):
    destination = tmp_path / "out" / "demo"
    destination.mkdir(parents=True)
    (destination / "old.txt").write_text("old", encoding="utf-8")

    with staged_dir(destination) as staging:
        assert staging == staging_dir(destination) == tmp_path / "out/demo.partial"
        assert (destination / "old.txt").exists()
        (staging / "new.txt").write_text("new", encoding="utf-8")

    assert sorted(p.name for p in destination.iterdir()) == ["new.txt"]
    assert sorted(p.name for p in destination.parent.iterdir()) == ["demo"]


def test_staged_dir_without_create_leaves_the_directory_to_the_body(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("a", encoding="utf-8")
    destination = tmp_path / "archive" / "26" / "demo"

    with staged_dir(destination, create=False) as staging:
        assert not staging.exists()
        assert staging.parent.is_dir()
        shutil.copytree(source, staging)

    assert (destination / "a.txt").read_text(encoding="utf-8") == "a"


def test_staged_dir_failure_removes_the_staging_and_keeps_the_old(tmp_path: Path):
    destination = tmp_path / "demo"
    destination.mkdir()
    (destination / "old.txt").write_text("old", encoding="utf-8")

    def fill_then_fail() -> None:
        with staged_dir(destination) as staging:
            (staging / "half.txt").write_text("half", encoding="utf-8")
            raise RuntimeError("render failed")

    with pytest.raises(RuntimeError):
        fill_then_fail()

    assert sorted(p.name for p in tmp_path.iterdir()) == ["demo"]
    assert (destination / "old.txt").exists()


def test_staged_dir_clears_a_stale_staging_directory(tmp_path: Path):
    destination = tmp_path / "demo"
    stale = staging_dir(destination)
    stale.mkdir()
    (stale / "leftover").write_text("crash", encoding="utf-8")

    with staged_dir(destination) as staging:
        assert list(staging.iterdir()) == []


def test_a_failed_swap_restores_the_old_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    destination = tmp_path / "demo"
    destination.mkdir()
    (destination / "old.txt").write_text("old", encoding="utf-8")
    real_replace = Path.replace

    def refuse_staging(self: Path, other: Path) -> Path:
        if self.name.endswith(STAGING_SUFFIX):
            raise PermissionError("share dropped")
        return real_replace(self, other)

    monkeypatch.setattr(Path, "replace", refuse_staging)
    with pytest.raises(PermissionError), staged_dir(destination) as staging:
        (staging / "new.txt").write_text("new", encoding="utf-8")

    assert sorted(p.name for p in tmp_path.iterdir()) == ["demo"]
    assert (destination / "old.txt").read_text(encoding="utf-8") == "old"


# --- exclusive_lock -------------------------------------------------------------


def test_exclusive_lock_waits_for_the_holder_and_cleans_up(tmp_path: Path):
    lock = tmp_path / ".lock"
    order: list[str] = []
    acquired = threading.Event()

    def contender() -> None:
        acquired.wait(5)
        with exclusive_lock(lock, timeout=5):
            order.append("contender")

    thread = threading.Thread(target=contender)
    with exclusive_lock(lock, timeout=5):
        assert lock.exists()
        thread.start()
        acquired.set()
        time.sleep(0.2)
        order.append("holder")
    thread.join(5)

    assert order == ["holder", "contender"]
    assert not lock.exists()


def test_exclusive_lock_times_out_naming_the_file(tmp_path: Path):
    lock = tmp_path / ".lock"
    lock.write_text("", encoding="utf-8")

    with (
        pytest.raises(TimeoutError, match=r"\.lock"),
        exclusive_lock(lock, timeout=0.1),
    ):
        pass
    assert lock.exists()


def test_exclusive_lock_is_released_when_the_body_fails(tmp_path: Path):
    lock = tmp_path / ".lock"
    with pytest.raises(RuntimeError), exclusive_lock(lock, timeout=1):
        raise RuntimeError("boom")
    assert not lock.exists()
