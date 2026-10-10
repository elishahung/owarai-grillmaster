"""ElevenLabs Scribe speech-to-text, one synchronous request per audio file.

The HTTP side is the SDK's `speech_to_text` client, reached through the
`SpeechToText` protocol and a `SpeechToTextFactory` so tests pass a fake
instead of patching. The API key goes to the factory (checked by the ASR
stage's preflight) and is never stored, logged or put in an error message
here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, BinaryIO, Literal, Protocol

from loguru import logger

from grillmaster.asr.errors import AsrError
from grillmaster.asr.payload import word_items
from grillmaster.core.fs import atomic_write_text

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pydantic import BaseModel

    from grillmaster.asr.payload import AsrPayload

# ElevenLabs Scribe list price; the only metered service in the pipeline.
PRICE_PER_HOUR_USD = 0.22
_REQUEST_TIMEOUT_S = 600.0
_SECONDS_PER_HOUR = 3600


@dataclass(frozen=True, slots=True)
class AsrOptions:
    """What to ask ElevenLabs for: the Scribe model and the language hint."""

    model: str
    language: str


@dataclass(frozen=True, slots=True)
class TranscriptionCost:
    """Billed audio length and its price."""

    audio_duration_s: float
    total_usd: float


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


# Builds the HTTP client from the API key.
type SpeechToTextFactory = Callable[[str], SpeechToText]


def connect_elevenlabs(api_key: str) -> SpeechToText:
    """The real ElevenLabs SDK client."""
    from elevenlabs.client import ElevenLabs  # noqa: PLC0415 - heavy, ASR only

    return ElevenLabs(api_key=api_key, timeout=_REQUEST_TIMEOUT_S).speech_to_text


def transcribe(
    api: SpeechToText, options: AsrOptions, audio: Path, output: Path
) -> TranscriptionCost:
    """Transcribe `audio` with word timings and speaker diarization, write
    the raw response to `output` atomically (so a partial file never stands
    in for a finished one), and return what the request cost."""
    if not audio.is_file():
        raise AsrError(f"Audio file not found: {audio}")
    logger.info(f"Submitting ElevenLabs STT request: {audio}")
    with audio.open("rb") as audio_file:
        response = api.convert(
            model_id=options.model,
            file=audio_file,
            language_code=options.language,
            timestamps_granularity="word",
            diarize=True,
        )
    payload: AsrPayload = response.model_dump(mode="json", exclude_none=True)
    atomic_write_text(output, json.dumps(payload, ensure_ascii=False, indent=4))
    cost = transcription_cost(payload)
    logger.info(
        f"ElevenLabs STT duration: {cost.audio_duration_s:.2f}s "
        f"(${cost.total_usd:.6f} at ${PRICE_PER_HOUR_USD:.2f}/hour)"
    )
    logger.success(f"Saved ElevenLabs STT response: {output}")
    return cost


def ensure_transcription(
    connect: Callable[[], SpeechToText],
    options: AsrOptions,
    audio: Path,
    output: Path,
) -> TranscriptionCost | None:
    """Make sure `output` holds the response for `audio`; returns the cost of
    the request made, or `None` when `output` already existed (`connect` is
    then never called).

    Existence alone is the hit check, deliberately (not a parse like the
    other caches): this is the one metered call, so a paid response is
    never requested again; a bad one is for `grill reset` to discard.
    """
    if output.exists():
        logger.info(f"Reusing ElevenLabs STT response: {output}")
        return None
    return transcribe(connect(), options, audio, output)


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
    duration_s = max(0.0, duration_s)
    return TranscriptionCost(
        audio_duration_s=duration_s,
        total_usd=duration_s / _SECONDS_PER_HOUR * PRICE_PER_HOUR_USD,
    )


def _latest_word_end(payload: AsrPayload) -> float | None:
    ends = [
        float(item["end"])
        for item in word_items(payload)
        if isinstance(item, dict) and isinstance(item.get("end"), int | float)
    ]
    return max(ends, default=None)
