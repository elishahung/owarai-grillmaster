"""The speech recognizers the project can call.

`[asr] provider` picks the one that transcribes a whole episode;
`[watch_clip] speech` the one that hears the proofread's spans, which must
be the other. Shared by config and the `asr` package, so it lives here.
"""

from __future__ import annotations

from enum import StrEnum


class AsrProvider(StrEnum):
    # Azure Speech's MAI-Transcribe-2 (`asr.mai`).
    MAI = "mai"
    # ElevenLabs Scribe v2 (`asr.scribe`).
    SCRIBE = "scribe"
