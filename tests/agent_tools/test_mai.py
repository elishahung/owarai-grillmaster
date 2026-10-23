from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from grillmaster.agent_tools.mai import (
    API_VERSION,
    MODEL,
    AzureSpeech,
    MaiError,
    MaiSpeechModel,
    Phrase,
    read_credentials,
)

if TYPE_CHECKING:
    from pathlib import Path

CREDENTIALS = AzureSpeech(endpoint="https://speech.example", key="secret")
RESULT = {
    "durationMilliseconds": 12_000,
    "combinedPhrases": [{"text": "なんでやねん。 えー。"}],
    "phrases": [
        {
            "offsetMilliseconds": 400,
            "durationMilliseconds": 900,
            "text": "なんでやねん。",
            "speaker": 1,
        },
        {
            "offsetMilliseconds": 2_250,
            "durationMilliseconds": 300,
            "text": "えー。",
            "speaker": 0,
        },
    ],
}


def _model(handler: httpx.MockTransport) -> MaiSpeechModel:
    return MaiSpeechModel(CREDENTIALS, timeout_s=10.0, transport=handler)


def test_a_clip_is_sent_as_verbatim_diarized_japanese(tmp_path: Path):
    audio = tmp_path / "audio.flac"
    audio.write_bytes(b"flac")
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=RESULT)

    transcript = _model(httpx.MockTransport(handle)).transcribe(audio, ["今井らいぱち"])

    assert transcript.phrases == (
        Phrase(start_s=0.4, speaker=1, text="なんでやねん。"),
        Phrase(start_s=2.25, speaker=0, text="えー。"),
    )
    assert transcript.audio_s == 12.0
    (request,) = seen
    assert str(request.url) == (
        "https://speech.example/speechtotext/transcriptions:transcribe"
        f"?api-version={API_VERSION}"
    )
    assert request.headers["Ocp-Apim-Subscription-Key"] == "secret"
    body = request.read().decode("utf-8")
    assert 'name="audio"; filename="audio.flac"' in body
    definition = json.loads(
        body.split('name="definition"\r\n\r\n')[1].split("\r\n--")[0]
    )
    assert definition == {
        "locales": ["ja"],
        "enhancedMode": {
            "enabled": True,
            "model": MODEL,
            "modelOptions": {"timestamps": "segment", "transcribeStyle": "verbatim"},
        },
        "diarization": {"enabled": True},
        "phraseList": {"phrases": ["今井らいぱち"]},
    }


def test_a_refused_request_is_a_mai_error(tmp_path: Path):
    audio = tmp_path / "audio.flac"
    audio.write_bytes(b"flac")
    transport = httpx.MockTransport(lambda _: httpx.Response(401, text="bad key"))
    with pytest.raises(MaiError, match="HTTP 401: bad key"):
        _model(transport).transcribe(audio, [])


@pytest.mark.parametrize(
    "body",
    [
        {"phrases": []},
        {
            "durationMilliseconds": 1,
            "phrases": [{"offsetMilliseconds": 0, "text": None}],
        },
        [],
    ],
    ids=["no-duration", "null-text", "not-an-object"],
)
def test_an_unexpected_result_is_a_mai_error(tmp_path: Path, body: object):
    audio = tmp_path / "audio.flac"
    audio.write_bytes(b"flac")
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    with pytest.raises(MaiError, match="unexpected transcription result"):
        _model(transport).transcribe(audio, [])


def test_the_credentials_come_from_the_env_file_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env_file = tmp_path / ".env"
    monkeypatch.setenv("AZURE_SPEECH_KEY", "from-env")
    env_file.write_text(
        "AZURE_SPEECH_ENDPOINT=https://speech.example/\n", encoding="utf-8"
    )
    with pytest.raises(MaiError, match="AZURE_SPEECH_KEY not set"):
        read_credentials(env_file)
    env_file.write_text(
        "AZURE_SPEECH_KEY=from-file\nAZURE_SPEECH_ENDPOINT=https://speech.example/\n",
        encoding="utf-8",
    )
    assert read_credentials(env_file) == AzureSpeech(
        endpoint="https://speech.example", key="from-file"
    )
