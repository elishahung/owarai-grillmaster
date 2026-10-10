"""The raw ElevenLabs speech-to-text response (`asr.json`)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from grillmaster.asr.errors import AsrError

if TYPE_CHECKING:
    from pathlib import Path

# The response as ElevenLabs returned it; only `words` / `transcripts` and
# `audio_duration_secs` are read.
type AsrPayload = dict[str, Any]


def read_payload(path: Path) -> AsrPayload:
    """Load `asr.json`; anything but a JSON object raises `AsrError`."""
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise AsrError(f"ASR response is not valid JSON: {path}: {error}") from None
    if not isinstance(payload, dict):
        raise AsrError(f"ASR response is not a JSON object: {path}")
    return payload


def word_items(payload: AsrPayload) -> list[Any]:
    """Every word entry, from `words` or, for multichannel responses, the
    concatenated `transcripts[*].words`. Entries are returned unchecked."""
    words = payload.get("words")
    if isinstance(words, list):
        return words
    transcripts = payload.get("transcripts")
    if not isinstance(transcripts, list):
        return []
    items: list[Any] = []
    for transcript in transcripts:
        if not isinstance(transcript, dict):
            continue
        channel_words = transcript.get("words")
        if isinstance(channel_words, list):
            items.extend(channel_words)
    return items
