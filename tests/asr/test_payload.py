from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from grillmaster.asr.errors import AsrError
from grillmaster.asr.payload import read_payload

if TYPE_CHECKING:
    from pathlib import Path


def test_read_payload_round_trips_utf8(tmp_path: Path):
    path = tmp_path / "asr.json"
    payload = {"text": "こんにちは", "words": []}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert read_payload(path) == payload


@pytest.mark.parametrize(
    ("content", "message"),
    [("[1, 2]", "not a JSON object"), ("{broken", "not valid JSON")],
)
def test_read_payload_rejects_unusable_files(
    tmp_path: Path, content: str, message: str
):
    path = tmp_path / "asr.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(AsrError, match=message):
        read_payload(path)
