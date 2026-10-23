"""Azure Speech's MAI-Transcribe-2: one clip's speech per request, priced by
its audio length.

The fast-transcription REST call (`transcriptions:transcribe`, multipart: the
FLAC `audio` and a JSON `definition`) with enhanced mode on MAI-Transcribe-2,
Japanese forced, the verbatim style (fillers, false starts and cut-off words
kept), speaker diarization and per-phrase times. Diarization numbers its
speakers per request, and on short clips it often splits one speaker in
two, so a number only marks a change of turn. `phrases` (the briefing's
names) bias recognition toward them; they are hints, not forced output.

Measured on 13 proofread spans (2026-10-23): no speech dropped, phrase times
match `ja.srt`, 2 to 5 s per request; names and homophones are often written
with the wrong kanji (尾形 → 緒方), which only the captions settle.

The key and endpoint are `AZURE_SPEECH_KEY` and `AZURE_SPEECH_ENDPOINT` in
the `.env` the manifest names, read from that file alone, as `meta` reads
its key. `httpx` and `dotenv` are imported on use.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from grillmaster.agent_tools._env import read_env_values
from grillmaster.core.models import FrozenModel

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    import httpx

API_VERSION = "2025-10-15"
MODEL = "MAI-Transcribe-2"
# Azure's introductory batch price, announced through 2026-12-31.
USD_PER_AUDIO_HOUR = 0.10
KEY_ENV = "AZURE_SPEECH_KEY"
ENDPOINT_ENV = "AZURE_SPEECH_ENDPOINT"
_SECONDS_PER_HOUR = 3600


class MaiError(Exception):
    """A request Azure refused or answered with something unreadable."""


class Phrase(FrozenModel):
    """One diarized phrase; `start_s` counts from the clip's start until the
    `watch_clip` tool moves it to the video's time for its record."""

    start_s: float
    speaker: int | None
    text: str


@dataclass(frozen=True)
class Transcript:
    phrases: tuple[Phrase, ...]
    audio_s: float

    @property
    def cost_usd(self) -> float:
        return self.audio_s / _SECONDS_PER_HOUR * USD_PER_AUDIO_HOUR


class SpeechModel(Protocol):
    """The part of `MaiSpeechModel` the `watch_clip` tool uses."""

    def transcribe(self, audio: Path, phrases: Sequence[str]) -> Transcript: ...


@dataclass(frozen=True)
class AzureSpeech:
    endpoint: str
    key: str


def read_credentials(env_file: Path) -> AzureSpeech:
    """The Azure Speech key and endpoint from `env_file` alone."""
    values = read_env_values(env_file, (KEY_ENV, ENDPOINT_ENV), MaiError)
    return AzureSpeech(endpoint=values[ENDPOINT_ENV].rstrip("/"), key=values[KEY_ENV])


def definition(phrases: Sequence[str]) -> dict[str, Any]:
    """The request's `definition` field."""
    body: dict[str, Any] = {
        "locales": ["ja"],
        "enhancedMode": {
            "enabled": True,
            "model": MODEL,
            "modelOptions": {"timestamps": "segment", "transcribeStyle": "verbatim"},
        },
        "diarization": {"enabled": True},
    }
    if phrases:
        body["phraseList"] = {"phrases": list(phrases)}
    return body


def parse_transcript(data: Mapping[str, Any]) -> Transcript:
    """The phrases and audio length of a `TranscribeResult`."""
    try:
        return Transcript(
            phrases=tuple(
                Phrase(
                    start_s=phrase["offsetMilliseconds"] / 1000,
                    speaker=phrase.get("speaker"),
                    text=phrase["text"],
                )
                for phrase in data.get("phrases", [])
            ),
            audio_s=data["durationMilliseconds"] / 1000,
        )
    # A missing field, a wrong type, or a body that is no object; pydantic's
    # errors are ValueErrors.
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        raise MaiError(f"unexpected transcription result: {error!r}") from error


class MaiSpeechModel:
    """MAI-Transcribe-2 at `credentials`; one request takes at most `timeout_s`."""

    def __init__(
        self,
        credentials: AzureSpeech,
        timeout_s: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._credentials = credentials
        self._timeout_s = timeout_s
        self._transport = transport

    def transcribe(self, audio: Path, phrases: Sequence[str]) -> Transcript:
        import httpx  # noqa: PLC0415 - imported on use

        url = (
            f"{self._credentials.endpoint}/speechtotext/transcriptions:transcribe"
            f"?api-version={API_VERSION}"
        )
        try:
            with httpx.Client(
                transport=self._transport, timeout=self._timeout_s
            ) as client:
                response = client.post(
                    url,
                    headers={"Ocp-Apim-Subscription-Key": self._credentials.key},
                    files={"audio": (audio.name, audio.read_bytes(), "audio/flac")},
                    data={"definition": json.dumps(definition(phrases))},
                )
        except (httpx.HTTPError, OSError) as error:
            raise MaiError(f"{type(error).__name__}: {error}") from error
        if response.status_code != httpx.codes.OK:
            raise MaiError(f"HTTP {response.status_code}: {response.text[:500]}")
        try:
            data = response.json()
        except ValueError as error:
            raise MaiError(f"unreadable transcription result: {error}") from error
        return parse_transcript(data)
