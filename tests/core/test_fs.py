from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from tests.fakes import refuse_rename, refuse_unlink

from grillmaster.core.fs import (
    COMPLETE_MARKER,
    SwapError,
    atomic_write_text,
    check_replaceable,
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
        atomic_write_text(path, 123)  # pyright: ignore[reportArgumentType] - a wrong type on purpose
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

    # The completion marker stays behind in no successful build.
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


def test_a_failed_swap_keeps_the_staging_and_restores_the_old_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    destination = tmp_path / "demo"
    destination.mkdir()
    (destination / "old.txt").write_text("old", encoding="utf-8")
    kept = staging_dir(destination)
    refuse_rename(monkeypatch, kept, "share dropped")
    with pytest.raises(SwapError) as caught, staged_dir(destination) as staging:
        (staging / "new.txt").write_text("new", encoding="utf-8")

    # Hours of finished work stay on disk, marked finished; the error says where.
    assert str(kept) in str(caught.value)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["demo", "demo.partial"]
    assert (kept / "new.txt").read_text(encoding="utf-8") == "new"
    assert (kept / COMPLETE_MARKER).exists()
    assert (destination / "old.txt").read_text(encoding="utf-8") == "old"


def test_a_stuck_marker_after_a_successful_swap_is_only_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings: list[str]
):
    destination = tmp_path / "demo"
    destination.mkdir()
    marker = destination / COMPLETE_MARKER
    refuse_unlink(monkeypatch, marker, "held open")
    with staged_dir(destination) as staging:
        (staging / "new.txt").write_text("new", encoding="utf-8")

    assert sorted(p.name for p in tmp_path.iterdir()) == ["demo"]
    assert (destination / "new.txt").read_text(encoding="utf-8") == "new"
    assert marker.exists()
    assert len(warnings) == 1
    assert str(marker) in warnings[0]


def test_a_locked_destination_fails_the_swap_and_keeps_the_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    destination = tmp_path / "demo"
    destination.mkdir()
    refuse_rename(monkeypatch, destination, "in use")
    with pytest.raises(SwapError, match=r"demo\.partial"), staged_dir(destination):
        pass

    assert sorted(p.name for p in tmp_path.iterdir()) == ["demo", "demo.partial"]


def test_a_kept_finished_build_is_never_silently_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    destination = tmp_path / "demo"
    destination.mkdir()
    with monkeypatch.context() as locked:
        refuse_rename(locked, destination, "in use")
        with pytest.raises(SwapError), staged_dir(destination) as staging:
            (staging / "video.mp4").write_bytes(b"hours of NVENC")

    # The next build (and the pre-render probe) refuse, naming the kept copy.
    kept = staging_dir(destination)
    with pytest.raises(SwapError, match=r"demo\.partial"), staged_dir(destination):
        pytest.fail("the body must not run")
    with pytest.raises(SwapError, match=r"demo\.partial"):
        check_replaceable(destination)
    assert (kept / "video.mp4").read_bytes() == b"hours of NVENC"


def test_staged_dir_restores_a_backup_left_by_a_crashed_swap(tmp_path: Path):
    destination = tmp_path / "demo"
    backup = tmp_path / "demo.old"
    backup.mkdir()
    (backup / "old.txt").write_text("old", encoding="utf-8")

    def fail() -> None:
        with staged_dir(destination):
            assert (destination / "old.txt").exists()
            raise RuntimeError("render failed")

    with pytest.raises(RuntimeError):
        fail()

    assert sorted(p.name for p in tmp_path.iterdir()) == ["demo"]
    assert (destination / "old.txt").read_text(encoding="utf-8") == "old"


# --- check_replaceable --------------------------------------------------------


def test_check_replaceable_probes_by_renaming_and_back(tmp_path: Path):
    destination = tmp_path / "demo"
    destination.mkdir()
    (destination / "video.mp4").write_bytes(b"v")

    check_replaceable(destination)
    check_replaceable(tmp_path / "missing")

    assert sorted(p.name for p in tmp_path.iterdir()) == ["demo"]
    assert (destination / "video.mp4").read_bytes() == b"v"


def test_check_replaceable_fails_on_a_locked_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    destination = tmp_path / "demo"
    destination.mkdir()

    refuse_rename(monkeypatch, destination, "held open by a player")
    with pytest.raises(SwapError, match="held open by a player"):
        check_replaceable(destination)
    assert destination.is_dir()


# --- exclusive_lock -------------------------------------------------------------


def test_exclusive_lock_waits_for_the_holder(tmp_path: Path):
    lock = tmp_path / "locks" / "a.lock"
    order: list[str] = []
    acquired = threading.Event()

    def contender() -> None:
        acquired.wait(5)
        with exclusive_lock(lock, timeout=5):
            order.append("contender")

    thread = threading.Thread(target=contender)
    with exclusive_lock(lock, timeout=5):
        thread.start()
        acquired.set()
        time.sleep(0.2)
        order.append("holder")
    thread.join(5)

    assert order == ["holder", "contender"]
    # The file stays: deleting it would let a waiter lock an orphan.
    assert lock.exists()


def test_exclusive_lock_times_out_naming_the_file(tmp_path: Path):
    lock = tmp_path / ".lock"
    with exclusive_lock(lock, timeout=0):
        with (
            pytest.raises(TimeoutError, match=r"\.lock"),
            exclusive_lock(lock, timeout=0.1),
        ):
            pass
        with pytest.raises(TimeoutError), exclusive_lock(lock, timeout=0):
            pass


def test_exclusive_lock_ignores_a_leftover_file(tmp_path: Path):
    lock = tmp_path / ".lock"
    lock.write_text("", encoding="utf-8")
    with exclusive_lock(lock, timeout=0):
        pass


def test_exclusive_lock_is_released_when_the_body_fails(tmp_path: Path):
    lock = tmp_path / ".lock"
    with pytest.raises(RuntimeError), exclusive_lock(lock, timeout=1):
        raise RuntimeError("boom")
    with exclusive_lock(lock, timeout=0):
        pass


_HOLD_LOCK = """
import sys, time
from pathlib import Path
from grillmaster.core.fs import exclusive_lock
with exclusive_lock(Path(sys.argv[1]), timeout=0):
    print("held", flush=True)
    time.sleep(60)
"""


def test_a_killed_holder_releases_the_lock(tmp_path: Path):
    lock = tmp_path / ".lock"
    holder = subprocess.Popen(  # noqa: TID251 - a separate process that dies holding the lock
        [sys.executable, "-c", _HOLD_LOCK, str(lock)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(TimeoutError), exclusive_lock(lock, timeout=0):
            pass
        holder.kill()  # a crash: no `finally` runs in the holder
        holder.wait(10)
        with exclusive_lock(lock, timeout=5):
            pass
    finally:
        holder.kill()
        holder.wait()
