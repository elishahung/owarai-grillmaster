"""ASR failures."""

from __future__ import annotations


class AsrError(Exception):
    """A speech recognizer (MAI-Transcribe-2, or Scribe for `watch_clip`)
    could not be called, or its response is unusable."""
