"""ElevenLabs Scribe v2 hearing `watch_clip` spans: the proofread's second
hearing, a different recognizer from the episode's MAI-Transcribe-2.

The HTTP side is the SDK's `speech_to_text` client, reached through the
`SpeechToText` protocol so tests pass a fake instead of patching. The API
key is read from the tool manifest's `.env` by `read_api_key` and is never
stored, logged or put in an error message here.

Japanese words come one character at a time with punctuation as its own
word, occasionally a punctuation mark glued in front (`。1934`).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, BinaryIO, Literal, Protocol

from loguru import logger

from grillmaster.asr.errors import AsrError
from grillmaster.asr.model import (
    Hearing,
    Phrase,
    TranscriptionCost,
    WordToken,
    split_leading_mark,
)
from grillmaster.asr.srt_builder import (
    JAPANESE_HARD_PUNCTUATION,
    ends_with_hard_punctuation,
)
from grillmaster.core.env_file import read_env_values

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pydantic import BaseModel

    from grillmaster.asr.payload import AsrPayload

MODEL = "scribe_v2"
LANGUAGE = "jpn"
# ElevenLabs Scribe list price.
PRICE_PER_HOUR_USD = 0.20
API_KEY_ENV = "ELEVENLABS_API_KEY"
IGNORED_WORD_TYPES = frozenset({"audio_event"})
# A clip phrase ends at a speaker change, a sentence end or this long a pause.
_PHRASE_PAUSE_S = 1.0
_SPEAKER_NUMBER = re.compile(r"(\d+)$")


class SpeechToText(Protocol):
    """The subset of the SDK's `client.speech_to_text` this module calls."""

    def convert(
        self,
        *,
        model_id: str,
        file: BinaryIO,
        language_code: str,
        timestamps_granularity: Literal["word"],
        diarize: bool,
    ) -> BaseModel: ...


def connect_elevenlabs(api_key: str, timeout_s: float) -> SpeechToText:
    """The real ElevenLabs SDK client; one request takes at most `timeout_s`."""
    from elevenlabs.client import ElevenLabs  # noqa: PLC0415 - heavy, ASR only

    return ElevenLabs(api_key=api_key, timeout=timeout_s).speech_to_text


def read_api_key(env_file: Path) -> str:
    """`ELEVENLABS_API_KEY` from `env_file` alone; `AsrError` when unset."""
    return read_env_values(env_file, (API_KEY_ENV,), AsrError)[API_KEY_ENV]


def _request(api: SpeechToText, audio: Path) -> AsrPayload:
    if not audio.is_file():
        raise AsrError(f"Audio file not found: {audio}")
    with audio.open("rb") as audio_file:
        response = api.convert(
            model_id=MODEL,
            file=audio_file,
            language_code=LANGUAGE,
            timestamps_granularity="word",
            diarize=True,
        )
    return response.model_dump(mode="json", exclude_none=True)


def transcription_cost(payload: AsrPayload) -> TranscriptionCost:
    """Price the response by its `audio_duration_secs`.

    Without it, the latest word end stands in; without timed words either,
    the cost is recorded as zero (with a warning) rather than failing a
    transcription that was already paid for.
    """
    duration = payload.get("audio_duration_secs")
    if isinstance(duration, int | float):
        duration_s = float(duration)
    else:
        logger.warning(
            "ElevenLabs STT response does not include audio_duration_secs; "
            "falling back to the latest word end timestamp"
        )
        latest_end = _latest_word_end(payload)
        if latest_end is None:
            logger.warning(
                "Unable to determine ElevenLabs STT audio duration; cost recorded as $0"
            )
        duration_s = latest_end or 0.0
    return TranscriptionCost.priced(duration_s, PRICE_PER_HOUR_USD)


def _latest_word_end(payload: AsrPayload) -> float | None:
    ends = [
        float(item["end"])
        for item in word_items(payload)
        if isinstance(item, dict) and isinstance(item.get("end"), int | float)
    ]
    return max(ends, default=None)


def word_items(payload: AsrPayload) -> list[Any]:
    """The `words` entries, unchecked (a clip is asked for one channel)."""
    words = payload.get("words")
    return words if isinstance(words, list) else []


def tokens(payload: AsrPayload) -> list[WordToken]:
    """Every timed entry but audio events (spacing included), a leading
    hard punctuation mark split off, sorted by time."""
    found: list[WordToken] = []
    for item in word_items(payload):
        if not isinstance(item, dict):
            continue
        if item.get("type") in IGNORED_WORD_TYPES:
            continue
        text = str(item.get("text") or "")
        if not text:
            continue
        start = item.get("start")
        end = item.get("end")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        if end < start:
            continue
        # Only a hard mark is split off (`。1934`), so it ends the phrase
        # before it; Scribe rarely glues a soft one in front.
        found.extend(
            split_leading_mark(
                WordToken(
                    text=text,
                    start=float(start),
                    end=float(end),
                    speaker_id=item.get("speaker_id"),
                ),
                JAPANESE_HARD_PUNCTUATION,
            )
        )
    found.sort(key=lambda token: (token.start, token.end))
    return found


class ScribeClipHearing:
    """Scribe on a `watch_clip` span: diarized words grouped into phrases.
    No keyterms (they cost extra). The SDK client is built on the first
    span, so the tool server starts without importing it."""

    def __init__(self, connect: Callable[[], SpeechToText]) -> None:
        self._connect = connect
        self._api: SpeechToText | None = None

    def hear(self, audio: Path) -> Hearing:
        if self._api is None:
            self._api = self._connect()
        payload = _request(self._api, audio)
        return Hearing(
            model=MODEL,
            phrases=clip_phrases(payload),
            cost=transcription_cost(payload),
        )


def clip_hearing(env_file: Path, timeout_s: float) -> ScribeClipHearing:
    """`watch_clip`'s hearing with the key in `env_file` (read now, so a
    missing key fails the tool server at once), each request bounded by
    `timeout_s`."""
    api_key = read_api_key(env_file)
    return ScribeClipHearing(lambda: connect_elevenlabs(api_key, timeout_s))


def clip_phrases(payload: AsrPayload) -> tuple[Phrase, ...]:
    """Words joined into phrases at each speaker change, sentence end or
    pause of `_PHRASE_PAUSE_S`; speakers numbered from `speaker_N`."""
    phrases: list[Phrase] = []
    current: list[WordToken] = []

    def close() -> None:
        text = "".join(token.text for token in current).strip()
        if text:
            phrases.append(
                Phrase(
                    start_s=round(current[0].start, 3),
                    speaker=_speaker_number(current[0].speaker_id),
                    text=text,
                )
            )
        current.clear()

    for token in tokens(payload):
        if current and (
            token.speaker_id != current[-1].speaker_id
            or token.start - current[-1].end > _PHRASE_PAUSE_S
        ):
            close()
        current.append(token)
        if ends_with_hard_punctuation(token.text):
            close()
    if current:
        close()
    return tuple(phrases)


def _speaker_number(speaker_id: str | None) -> int | None:
    match = _SPEAKER_NUMBER.search(speaker_id or "")
    return int(match.group(1)) if match else None
