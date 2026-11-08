"""A provider's raw speech-to-text response, kept as `asr.json`."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from grillmaster.asr.errors import AsrError

if TYPE_CHECKING:
    from pathlib import Path

# The response as the provider returned it; `asr.providers` reads it.
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
