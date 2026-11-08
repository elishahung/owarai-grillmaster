from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.fakes import FakeSpeechToText

from grillmaster.asr.errors import AsrError
from grillmaster.asr.model import Phrase, WordToken
from grillmaster.asr.scribe import (
    PRICE_PER_HOUR_USD,
    ScribeClipHearing,
    read_api_key,
    tokens,
    transcription_cost,
    word_items,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def audio(tmp_path: Path) -> Path:
    path = tmp_path / "audio.ogg"
    path.write_bytes(b"audio")
    return path


def word(
    text: str, start: float, end: float, speaker: str = "speaker_0", kind: str = "word"
) -> dict[str, object]:
    return {
        "text": text,
        "start": start,
        "end": end,
        "type": kind,
        "speaker_id": speaker,
    }


def test_a_clip_is_sent_with_word_timings_and_diarization(audio: Path):
    api = FakeSpeechToText({"text": "", "words": [], "audio_duration_secs": 1800})

    hearing = ScribeClipHearing(lambda: api).hear(audio)

    assert api.calls == [
        {
            "model_id": "scribe_v2",
            "file": b"audio",
            "language_code": "jpn",
            "timestamps_granularity": "word",
            "diarize": True,
        }
    ]
    assert hearing.cost.total_usd == pytest.approx(PRICE_PER_HOUR_USD / 2)


def test_missing_audio_fails_before_the_request(tmp_path: Path):
    api = FakeSpeechToText({})

    with pytest.raises(AsrError, match="Audio file not found"):
        ScribeClipHearing(lambda: api).hear(tmp_path / "missing.flac")
    assert api.calls == []


def test_cost_from_audio_duration_secs():
    cost = transcription_cost({"audio_duration_secs": 7200})

    assert cost.audio_duration_s == 7200.0
    assert cost.total_usd == PRICE_PER_HOUR_USD * 2


def test_cost_falls_back_to_the_latest_word_end():
    cost = transcription_cost(
        {
            "words": [
                {"text": "a", "start": 0.0, "end": 1.5},
                {"text": "b", "start": 1.5, "end": 9.0},
                {"text": "c", "start": 2.0, "end": 3.0},
            ]
        }
    )

    assert cost.audio_duration_s == 9.0
    assert cost.total_usd == pytest.approx(9.0 / 3600 * PRICE_PER_HOUR_USD)


@pytest.mark.parametrize(
    "payload",
    [{"text": "こんにちは", "words": []}, {"words": [{"text": "a"}]}, {}],
)
def test_cost_without_any_duration_is_zero(payload: dict[str, object]):
    cost = transcription_cost(payload)

    assert cost.audio_duration_s == 0.0
    assert cost.total_usd == 0.0


def test_word_items_are_the_words():
    words = [{"text": "a"}]

    assert word_items({"words": words}) == words


@pytest.mark.parametrize("payload", [{}, {"words": None}])
def test_word_items_without_words_is_empty(payload: dict[str, object]):
    assert word_items(payload) == []


def test_tokens_split_a_leading_hard_mark_and_sort_by_time():
    payload = {
        "words": [
            word("。1934", 3.0, 3.6),
            word("(笑)", 1.0, 1.5, kind="audio_event"),
            word("年", 3.6, 3.8),
            word("昭和", 2.0, 2.9),
        ]
    }

    assert tokens(payload) == [
        WordToken("昭和", 2.0, 2.9, "speaker_0"),
        WordToken("。", 3.0, 3.0, "speaker_0"),
        WordToken("1934", 3.0, 3.6, "speaker_0"),
        WordToken("年", 3.6, 3.8, "speaker_0"),
    ]


def test_a_clip_is_heard_as_phrases_split_at_turns_sentences_and_pauses(
    audio: Path,
):
    api = FakeSpeechToText(
        {
            "text": "",
            "audio_duration_secs": 30.0,
            "words": [
                word("な", 0.5, 0.6, "speaker_2"),
                word("ん", 0.6, 0.7, "speaker_2"),
                word("で", 0.7, 0.8, "speaker_2"),
                word("。", 0.8, 0.8, "speaker_2"),
                word("え", 1.0, 1.1, "speaker_0"),
                word("ー", 1.1, 1.2, "speaker_0"),
                word("ま", 5.0, 5.1, "speaker_0"),
                word("た", 5.1, 5.2, "speaker_0"),
            ],
        }
    )

    hearing = ScribeClipHearing(lambda: api).hear(audio)

    assert hearing.model == "scribe_v2"
    assert hearing.phrases == (
        Phrase(start_s=0.5, speaker=2, text="なんで。"),
        Phrase(start_s=1.0, speaker=0, text="えー"),
        Phrase(start_s=5.0, speaker=0, text="また"),
    )
    assert hearing.cost.total_usd == pytest.approx(30 / 3600 * PRICE_PER_HOUR_USD)


def test_the_key_comes_from_the_env_file_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env_file = tmp_path / ".env"
    monkeypatch.setenv("ELEVENLABS_API_KEY", "from-env")
    with pytest.raises(AsrError, match="ELEVENLABS_API_KEY not set in"):
        read_api_key(env_file)
    env_file.write_text("ELEVENLABS_API_KEY=from-file\n", encoding="utf-8")
    assert read_api_key(env_file) == "from-file"
