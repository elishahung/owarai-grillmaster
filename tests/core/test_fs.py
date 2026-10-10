from __future__ import annotations

from pathlib import Path

import pytest

from grillmaster.core.fs import atomic_write_text


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
