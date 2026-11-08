"""What every speech provider hands back, whatever its response looks like.

A whole-episode response becomes `WordToken`s for the SRT builder; a short
clip becomes a `Hearing` of diarized `Phrase`s for `watch_clip`. Prices are
per audio length, so each request reports a `TranscriptionCost`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from grillmaster.core.models import FrozenModel

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

_SECONDS_PER_HOUR = 3600


@dataclass(frozen=True)
class WordToken:
    """One timed word (in Japanese mostly one character or a punctuation
    mark); `speaker_id` is the provider's diarized label."""

    text: str
    start: float
    end: float
    speaker_id: str | None


@dataclass(frozen=True, slots=True)
class TranscriptionCost:
    """Billed audio length and its price."""

    audio_duration_s: float
    total_usd: float

    @classmethod
    def priced(cls, audio_duration_s: float, usd_per_hour: float) -> TranscriptionCost:
        duration = max(0.0, audio_duration_s)
        return cls(duration, duration / _SECONDS_PER_HOUR * usd_per_hour)


def split_leading_mark(token: WordToken, marks: str) -> list[WordToken]:
    """`token` with a leading mark from `marks` split off as its own
    zero-length token (`。1934` → `。` + `1934`), else `[token]`."""
    if len(token.text) <= 1 or token.text[0] not in marks:
        return [token]
    return [
        WordToken(token.text[0], token.start, token.start, token.speaker_id),
        WordToken(token.text[1:], token.start, token.end, token.speaker_id),
    ]


class Phrase(FrozenModel):
    """One diarized phrase; `start_s` counts from the clip's start until the
    `watch_clip` tool moves it to the video's time for its record."""

    start_s: float
    speaker: int | None
    text: str


@dataclass(frozen=True)
class Hearing:
    """A clip's speech as one provider heard it."""

    model: str
    phrases: tuple[Phrase, ...]
    cost: TranscriptionCost


class ClipHearing(Protocol):
    """A provider's clip transcription, as `watch_clip` uses it. `phrases`
    (the briefing's cast names) are hints a provider may ignore."""

    def hear(self, audio: Path, phrases: Sequence[str]) -> Hearing: ...
