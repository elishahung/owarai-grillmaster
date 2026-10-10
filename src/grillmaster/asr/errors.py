"""ASR failures."""

from __future__ import annotations


class AsrError(Exception):
    """ElevenLabs could not be called, or its response is unusable."""
