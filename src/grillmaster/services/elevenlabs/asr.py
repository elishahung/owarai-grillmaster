"""ElevenLabs Speech to Text integration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from elevenlabs.client import ElevenLabs
from loguru import logger

from grillmaster.settings import settings

ELEVENLABS_STT_PRICE_PER_HOUR_USD = 0.22


@dataclass(frozen=True)
class ElevenLabsTranscriptionResult:
    """Accounting summary for a completed ElevenLabs transcription."""

    audio_duration_secs: float
    total_cost: float


class ElevenLabsASR:
    """Client for synchronous ElevenLabs Scribe transcription."""

    def __init__(self) -> None:
        if not settings.elevenlabs_api_key:
            raise ValueError("ElevenLabs ASR requires ELEVENLABS_API_KEY")
        self.client = ElevenLabs(
            api_key=settings.elevenlabs_api_key,
            timeout=600,
        )

    def transcribe_to_file(
        self,
        audio_path: Path,
        json_path: Path,
    ) -> ElevenLabsTranscriptionResult:
        """Transcribe an audio file and persist the raw JSON response."""
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        logger.info(f"Submitting ElevenLabs STT request: {audio_path}")
        with audio_path.open("rb") as audio_file:
            response = self.client.speech_to_text.convert(
                file=audio_file,
                model_id=settings.elevenlabs_stt_model,
                language_code=settings.elevenlabs_stt_language_code,
                timestamps_granularity="word",
                diarize=True,
            )

        payload = _to_jsonable(response)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=4),
            encoding="utf-8",
        )
        result = calculate_transcription_cost(payload)
        logger.info(
            f"ElevenLabs STT duration: {result.audio_duration_secs:.2f}s "
            f"(${result.total_cost:.6f} at "
            f"${ELEVENLABS_STT_PRICE_PER_HOUR_USD:.2f}/hour)"
        )
        logger.success(f"Saved ElevenLabs STT response: {json_path}")
        return result


def _to_jsonable(value: Any) -> Any:
    """Convert SDK response objects into JSON-serializable data."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    return value


def calculate_transcription_cost(
    payload: dict[str, Any],
) -> ElevenLabsTranscriptionResult:
    """Calculate ElevenLabs STT cost from response duration metadata."""
    duration_secs = _extract_audio_duration_secs(payload)
    if duration_secs is None:
        logger.warning(
            "ElevenLabs STT response does not include audio_duration_secs; "
            "falling back to the latest word end timestamp"
        )
        duration_secs = _extract_duration_from_words(payload)

    if duration_secs is None:
        logger.warning(
            "Unable to determine ElevenLabs STT audio duration; cost recorded as $0"
        )
        duration_secs = 0.0

    duration_secs = max(0.0, duration_secs)
    total_cost = (duration_secs / 3600) * ELEVENLABS_STT_PRICE_PER_HOUR_USD
    return ElevenLabsTranscriptionResult(
        audio_duration_secs=duration_secs,
        total_cost=total_cost,
    )


def _extract_audio_duration_secs(payload: dict[str, Any]) -> float | None:
    duration = payload.get("audio_duration_secs")
    if isinstance(duration, (int, float)):
        return float(duration)
    return None


def _extract_duration_from_words(payload: dict[str, Any]) -> float | None:
    latest_end: float | None = None
    for word in _extract_word_items(payload):
        if not isinstance(word, dict):
            continue
        end = word.get("end")
        if not isinstance(end, (int, float)):
            continue
        latest_end = max(latest_end or 0.0, float(end))
    return latest_end


def _extract_word_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("words"), list):
        return payload["words"]

    transcripts = payload.get("transcripts")
    if isinstance(transcripts, list):
        words: list[dict[str, Any]] = []
        for transcript in transcripts:
            if isinstance(transcript, dict) and isinstance(
                transcript.get("words"), list
            ):
                words.extend(transcript["words"])
        return words

    return []
