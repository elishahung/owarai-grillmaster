"""The speech providers, one entry each: the one place that branches on a
provider.

`PROVIDERS` maps `AsrProvider` to what callers need from a provider module:
how to recognize and read its kept `asr.json` (`for_response`, so a project
transcribed before `[asr] provider` changed still builds its SRT), its SRT
knobs, the `.env` keys it needs and its `watch_clip` hearing. The ASR stage
still calls each episode client itself, since their uploads differ (Scribe
reads the Opus, MAI a FLAC). Drop a provider by deleting its module, its
`AsrProvider` member and its entry here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from grillmaster.asr import mai, scribe
from grillmaster.asr.errors import AsrError
from grillmaster.asr.srt_builder import SrtFormatOptions
from grillmaster.core.asr_provider import AsrProvider

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from grillmaster.asr.model import ClipHearing, WordToken
    from grillmaster.asr.payload import AsrPayload


@dataclass(frozen=True)
class Provider:
    label: str
    is_response: Callable[[AsrPayload], bool]
    # The SRT builder's time-ordered tokens.
    srt_tokens: Callable[[AsrPayload], list[WordToken]]
    # The words in response order, for the timing checks.
    timed_words: Callable[[AsrPayload], list[WordToken]]
    srt_options: SrtFormatOptions
    # Read from the tool manifest's `.env` alone.
    env_keys: tuple[str, ...]
    # `(env_file, timeout_s)`; raises `AsrError` without the keys.
    clip_hearing: Callable[[Path, float], ClipHearing]


PROVIDERS: dict[AsrProvider, Provider] = {
    AsrProvider.MAI: Provider(
        label="MAI-Transcribe-2",
        is_response=mai.is_response,
        srt_tokens=mai.srt_tokens,
        timed_words=mai.timed_words,
        srt_options=mai.SRT_OPTIONS,
        env_keys=(mai.KEY_ENV, mai.ENDPOINT_ENV),
        clip_hearing=mai.clip_hearing,
    ),
    AsrProvider.SCRIBE: Provider(
        label="ElevenLabs Scribe",
        is_response=scribe.is_response,
        srt_tokens=scribe.srt_tokens,
        timed_words=scribe.timed_words,
        srt_options=SrtFormatOptions(),
        env_keys=(scribe.API_KEY_ENV,),
        clip_hearing=scribe.clip_hearing,
    ),
}


def for_response(payload: AsrPayload) -> Provider:
    """The provider whose response `payload` is."""
    for provider in PROVIDERS.values():
        if provider.is_response(payload):
            return provider
    raise AsrError(f"Unknown ASR response shape (keys: {sorted(payload)[:8]})")
