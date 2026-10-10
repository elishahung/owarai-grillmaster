from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel
from tests.fakes import FakeSpeechToText

from grillmaster.asr.client import (
    PRICE_PER_HOUR_USD,
    AsrOptions,
    ElevenLabsAsr,
    ensure_transcription,
    transcription_cost,
)
from grillmaster.asr.errors import AsrError

if TYPE_CHECKING:
    from pathlib import Path

OPTIONS = AsrOptions(model="scribe_v2", language="jpn")


class SdkResponse(BaseModel):
    """Stands in for the SDK's pydantic response model."""

    text: str
    words: list[dict[str, object]]
    language_code: str | None = None


@pytest.fixture
def audio(tmp_path: Path) -> Path:
    path = tmp_path / "audio.ogg"
    path.write_bytes(b"audio")
    return path


def test_sends_the_audio_with_word_timings_and_diarization(tmp_path: Path, audio: Path):
    response = {"text": "こんにちは", "words": [], "audio_duration_secs": 1800}
    api = FakeSpeechToText(response)
    output = tmp_path / "asr" / "asr.json"

    cost = ElevenLabsAsr(OPTIONS, "secret-key", connect=api.connect).transcribe(
        audio, output
    )

    assert api.api_keys == ["secret-key"]
    assert api.calls == [
        {
            "model_id": "scribe_v2",
            "file": b"audio",
            "language_code": "jpn",
            "timestamps_granularity": "word",
            "diarize": True,
        }
    ]
    assert json.loads(output.read_text(encoding="utf-8")) == response
    assert cost.audio_duration_s == 1800.0
    assert cost.total_usd == pytest.approx(PRICE_PER_HOUR_USD / 2)
    assert [path.name for path in output.parent.iterdir()] == ["asr.json"]


def test_writes_the_sdk_model_without_none_fields(tmp_path: Path, audio: Path):
    api = FakeSpeechToText(SdkResponse(text="こんにちは", words=[]))
    output = tmp_path / "asr.json"

    ElevenLabsAsr(OPTIONS, "key", connect=api.connect).transcribe(audio, output)

    assert json.loads(output.read_text(encoding="utf-8")) == {
        "text": "こんにちは",
        "words": [],
    }


def test_rejects_a_non_object_response(tmp_path: Path, audio: Path):
    api = FakeSpeechToText(["not", "an", "object"])
    output = tmp_path / "asr.json"

    with pytest.raises(AsrError, match="Unexpected ElevenLabs STT response"):
        ElevenLabsAsr(OPTIONS, "key", connect=api.connect).transcribe(audio, output)
    assert not output.exists()


def test_missing_audio_fails_before_the_request(tmp_path: Path):
    api = FakeSpeechToText({})

    with pytest.raises(AsrError, match="Audio file not found"):
        ElevenLabsAsr(OPTIONS, "key", connect=api.connect).transcribe(
            tmp_path / "missing.ogg", tmp_path / "asr.json"
        )
    assert api.calls == []


def test_empty_api_key_is_refused_without_connecting():
    api = FakeSpeechToText({})

    with pytest.raises(AsrError, match="ELEVENLABS_API_KEY"):
        ElevenLabsAsr(OPTIONS, "", connect=api.connect)
    assert api.api_keys == []


def test_ensure_transcription_requests_on_a_miss(tmp_path: Path, audio: Path):
    api = FakeSpeechToText({"text": "", "words": [], "audio_duration_secs": 3600})
    output = tmp_path / "asr.json"

    cost = ensure_transcription(
        audio, output, options=OPTIONS, api_key="k", connect=api.connect
    )

    assert cost is not None
    assert cost.total_usd == pytest.approx(PRICE_PER_HOUR_USD)
    assert output.is_file()


def test_an_existing_response_is_never_paid_for_again(tmp_path: Path, audio: Path):
    """Even an unparsable one: existence alone is the hit check."""
    api = FakeSpeechToText({})
    output = tmp_path / "asr.json"
    output.write_text("{ truncated", encoding="utf-8")

    cost = ensure_transcription(
        audio, output, options=OPTIONS, api_key="k", connect=api.connect
    )

    assert cost is None
    assert api.api_keys == []
    assert output.read_text(encoding="utf-8") == "{ truncated"


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
