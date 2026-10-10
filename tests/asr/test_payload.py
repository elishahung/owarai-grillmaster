from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from grillmaster.asr.errors import AsrError
from grillmaster.asr.payload import read_payload, word_items

if TYPE_CHECKING:
    from pathlib import Path


def test_word_items_prefers_top_level_words():
    words = [{"text": "a"}]
    payload = {"words": words, "transcripts": [{"words": [{"text": "b"}]}]}

    assert word_items(payload) == words


def test_word_items_concatenates_channel_transcripts():
    payload = {
        "transcripts": [
            {"words": [{"text": "a"}]},
            "not a transcript",
            {"text": "no words"},
            {"words": [{"text": "b"}, {"text": "c"}]},
        ]
    }

    assert word_items(payload) == [{"text": "a"}, {"text": "b"}, {"text": "c"}]


@pytest.mark.parametrize("payload", [{}, {"words": None}, {"transcripts": {}}])
def test_word_items_without_words_is_empty(payload: dict[str, object]):
    assert word_items(payload) == []


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
