"""Find collapsed word timings in a response.

ElevenLabs Scribe, the former ASR, occasionally stacked hundreds of words on
one timestamp, often repeating text from elsewhere in the episode. The SRT
builder would then push every block in the stack 0.35 s after the previous
one, so tens of seconds of speech flash by as a burst of unreadable, partly
duplicated subtitles. Nothing downstream can repair that; the ASR stage
discards such a response and fails, so the next run requests a new
transcription. MAI-Transcribe-2 showed no stack in 18 responses
(2026-11-09); the check stays as a guard.

Measured over 1048 archived Scribe responses (2026-11-09): stacks of 40-90
words are common (chants, rapid repeats), while the six stacks of 100 or
more were all collapsed speech, four of them 87-100% text repeated from
elsewhere in the same response.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.core.timecode import format_clock

if TYPE_CHECKING:
    from collections.abc import Sequence

    from grillmaster.asr.model import WordToken

# Words starting within this span of a stack's first word share its timestamp.
STACK_WINDOW_S = 0.05
# A stack of this many words or more counts as collapsed timings.
COLLAPSED_STACK_WORDS = 100
_PREVIEW_CHARS = 30


def collapsed_stacks(words: Sequence[WordToken]) -> list[str]:
    """One description (`MM:SS: N words (text…)`) per run of
    `COLLAPSED_STACK_WORDS` or more consecutive `words` (response order,
    `mai.timed_words`) whose starts lie within `STACK_WINDOW_S` of
    the run's first word."""
    stacks: list[str] = []
    first = 0
    while first < len(words):
        last = first
        while (
            last + 1 < len(words)
            and abs(words[last + 1].start - words[first].start) < STACK_WINDOW_S
        ):
            last += 1
        count = last - first + 1
        if count >= COLLAPSED_STACK_WORDS:
            text = "".join(word.text for word in words[first : last + 1])
            stacks.append(
                f"{format_clock(words[first].start)}: {count} words "
                f"({text[:_PREVIEW_CHARS]}…)"
            )
        first = last + 1
    return stacks
