from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from grillmaster.core.json_artifact import (
    load_adapted,
    load_model,
    read_model,
    write_model,
)

if TYPE_CHECKING:
    from pathlib import Path


class Sample(BaseModel):
    name: str
    count: int


def test_write_then_load_round_trips_unicode(tmp_path: Path):
    path = tmp_path / "sample.json"
    write_model(path, Sample(name="松本", count=3))
    assert "松本" in path.read_text(encoding="utf-8")
    assert load_model(path, Sample) == Sample(name="松本", count=3)


def test_load_model_missing_is_none(tmp_path: Path):
    assert load_model(tmp_path / "missing.json", Sample) is None


@pytest.mark.parametrize(
    "content",
    [b'{"name": "x"', b'{"name": "x"}', b"\xff\xfe\x00garbage", b""],
    ids=["truncated", "invalid", "undecodable", "empty"],
)
def test_load_model_corrupt_is_none(tmp_path: Path, content: bytes):
    path = tmp_path / "bad.json"
    path.write_bytes(content)
    assert load_model(path, Sample) is None


def test_load_model_accepts_bom(tmp_path: Path):
    path = tmp_path / "bom.json"
    path.write_bytes(b'\xef\xbb\xbf{"name": "x", "count": 1}')
    assert load_model(path, Sample) == Sample(name="x", count=1)


def test_load_adapted(tmp_path: Path):
    adapter = TypeAdapter(list[int])
    path = tmp_path / "list.json"
    assert load_adapted(path, adapter) is None
    path.write_text("[1, 2]", encoding="utf-8")
    assert load_adapted(path, adapter) == [1, 2]
    path.write_text('["x"]', encoding="utf-8")
    assert load_adapted(path, adapter) is None
    path.write_bytes(b"\xff\xfe")
    assert load_adapted(path, adapter) is None


def test_read_model_raises_on_missing_and_corrupt(tmp_path: Path):
    path = tmp_path / "strict.json"
    with pytest.raises(FileNotFoundError):
        read_model(path, Sample)
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValidationError):
        read_model(path, Sample)
    write_model(path, Sample(name="ok", count=0))
    assert read_model(path, Sample) == Sample(name="ok", count=0)
