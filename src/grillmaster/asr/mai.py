"""Azure Speech's MAI-Transcribe-2: the episode's ASR, priced by audio
length.

One fast-transcription REST call (`transcriptions:transcribe`, multipart:
the FLAC `audio` and a JSON `definition`) per episode, enhanced mode on
MAI-Transcribe-2, Japanese forced, the verbatim style (fillers, false
starts and cut-off words kept), word times, speaker diarization. `phrases`
(cast names) bias recognition toward them; they are hints, not forced
output. The REST reference
caps a file at 2 hours and 250 MB; 90 minutes of 16 kHz mono FLAC is about
200 MB. MAI reads WAV, MP3 or FLAC, not Opus.

Measured (2026-11-09, `asr-provider-evaluation.md`): 11 of 11 diarized
90-minute requests answered in 91-117 s; no stretched words or collapsed
stacks where Scribe had them; more back-channels and quick exchanges kept
as their own speaker turns. One response came back with every phrase's
offset and duration 0 while its word times were right, so tokens take the
word times alone. Names and homophones are often written with the wrong
kanji (尾形 → 緒方), which only the captions settle.

The response is kept raw as `asr.json`: `phrases[{speaker, text,
words[{text, offsetMilliseconds, durationMilliseconds}]}]` and
`durationMilliseconds`. Japanese words come one character at a time with
punctuation as its own word, now and then a punctuation mark glued in front
(`、YouTube`). A kept response of another shape (an ElevenLabs Scribe
`asr.json` from before MAI) is refused by `read_response`, not converted.
The key and endpoint come from the ASR stage's secrets. `httpx` is
imported on use.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from itertools import groupby
from typing import TYPE_CHECKING, Any, Protocol

from loguru import logger

from grillmaster.asr.errors import AsrError
from grillmaster.asr.model import TranscriptionCost, WordToken, split_leading_mark
from grillmaster.asr.payload import read_payload
from grillmaster.asr.srt_builder import (
    JAPANESE_HARD_PUNCTUATION,
    JAPANESE_SOFT_PUNCTUATION,
    MIN_SEGMENT_DURATION_S,
    ends_with_hard_punctuation,
)
from grillmaster.core.fs import atomic_write_text

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    import httpx

    from grillmaster.asr.payload import AsrPayload

API_VERSION = "2025-10-15"
MODEL = "MAI-Transcribe-2"
LANGUAGE = "ja"
# Azure's introductory batch price, announced through 2026-12-31.
USD_PER_AUDIO_HOUR = 0.10
KEY_ENV = "AZURE_SPEECH_KEY"
ENDPOINT_ENV = "AZURE_SPEECH_ENDPOINT"
# An episode request: a 90-minute file answered in under 2 minutes; the
# service's own timeout (HTTP 408) comes after about 4.
EPISODE_TIMEOUT_S = 900.0
# Phrases sent at most, the first ones kept: Azure documents no limit for
# a fast-transcription phrase list (a 2026 write-up cites 2,000), and a
# compilation merges every entry's credits.
MAX_PHRASES = 500
# MAI rejects the whole request (HTTP 400) when any phrase is longer.
MAX_PHRASE_CHARS = 50
# Statuses worth one more try: a timeout, throttling, a server-side failure
# (diarization has failed this way for whole regions at a time).
_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

# MAI glues 、 as often as 。 in front of a word (`、YouTube`); both come
# off, so the mark ends the text before it.
_LEADING_MARKS = JAPANESE_HARD_PUNCTUATION + JAPANESE_SOFT_PUNCTUATION

# Back-channel words (相槌), one or more in a row, e.g. うん, はいはい.
# A lone あ is one too: a listener's short 「あ。」 just before the talker
# goes on.
_BACKCHANNEL_WORDS = (
    "なるほど",
    "うーん",
    "ふーん",
    "うん",
    "はい",
    "ええ",
    "えー",
    "へえ",
    "へー",
    "ほう",
    "ほー",
    "ああ",
    "あー",
    "おお",
    "おー",
    "はあ",
    "あ",
)
_BACKCHANNEL = re.compile("(?:" + "|".join(_BACKCHANNEL_WORDS) + ")+")
# A sentence ending in か。 asks a question its back-channel answers,
# unless it is a rhetorical 〜じゃないですか。 (「めっちゃあるじゃないですか。」
# 「うん。」), which only seeks agreement.
_RHETORICAL_TAILS = ("じゃないですか。", "やないですか。", "じゃないか。", "やないか。")
# MAI times one character per token: enough tokens for the longest tail.
_RHETORICAL_TAIL_TOKENS = max(map(len, _RHETORICAL_TAILS))


class MaiError(AsrError):
    """A request Azure refused or answered with something unreadable."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class AzureSpeech:
    endpoint: str
    key: str


def definition(phrases: Sequence[str]) -> dict[str, Any]:
    """The request's `definition` field."""
    body: dict[str, Any] = {
        "locales": [LANGUAGE],
        "enhancedMode": {
            "enabled": True,
            "model": MODEL,
            "modelOptions": {
                "timestamps": "word",
                "transcribeStyle": "verbatim",
            },
        },
        "diarization": {"enabled": True},
    }
    kept: list[str] = []
    too_long: list[str] = []
    for phrase in phrases:
        (kept if len(phrase) <= MAX_PHRASE_CHARS else too_long).append(phrase)
    if too_long:
        logger.warning(
            f"Leaving {len(too_long)} phrase(s) over {MAX_PHRASE_CHARS} characters "
            f"out of the MAI phrase list: {too_long}"
        )
    if kept:
        body["phraseList"] = {"phrases": kept[:MAX_PHRASES]}
    return body


class MaiClient:
    """MAI-Transcribe-2 at `credentials`; one request takes at most `timeout_s`."""

    def __init__(
        self,
        credentials: AzureSpeech,
        timeout_s: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._credentials = credentials
        self._timeout_s = timeout_s
        self._transport = transport

    def transcribe(
        self, audio: Path, output: Path, phrases: Sequence[str]
    ) -> TranscriptionCost:
        """Transcribe a whole episode's FLAC `audio` with word times, write
        the raw response to `output` atomically, and return its cost. A
        timeout, throttling or server error is tried once more."""
        logger.info(f"Submitting MAI-Transcribe-2 request: {audio}")
        body = definition(phrases)
        try:
            data = self._post(audio, body)
        except MaiError as error:
            if not _retryable(error):
                raise
            logger.warning(f"MAI-Transcribe-2 request failed, retrying: {error}")
            data = self._post(audio, body)
        if not timed_words(data):
            raise MaiError("MAI-Transcribe-2 returned no timed words")
        atomic_write_text(output, json.dumps(data, ensure_ascii=False, indent=4))
        cost = transcription_cost(data)
        logger.info(
            f"MAI-Transcribe-2 duration: {cost.audio_duration_s:.2f}s "
            f"(${cost.total_usd:.6f} at ${USD_PER_AUDIO_HOUR:.2f}/hour)"
        )
        logger.success(f"Saved MAI-Transcribe-2 response: {output}")
        return cost

    def _post(self, audio: Path, body: Mapping[str, Any]) -> dict[str, Any]:
        import httpx  # noqa: PLC0415 - imported on use

        url = (
            f"{self._credentials.endpoint.rstrip('/')}"
            f"/speechtotext/transcriptions:transcribe?api-version={API_VERSION}"
        )
        try:
            # Streamed from the file: an episode's FLAC is ~120 MB.
            with (
                audio.open("rb") as upload,
                httpx.Client(
                    transport=self._transport, timeout=self._timeout_s
                ) as client,
            ):
                response = client.post(
                    url,
                    headers={"Ocp-Apim-Subscription-Key": self._credentials.key},
                    files={"audio": (audio.name, upload, "audio/flac")},
                    data={"definition": json.dumps(body, ensure_ascii=False)},
                )
        except (httpx.HTTPError, OSError) as error:
            raise MaiError(f"{type(error).__name__}: {error}") from error
        if response.status_code != httpx.codes.OK:
            raise MaiError(
                f"HTTP {response.status_code}: {response.text[:500]}",
                status=response.status_code,
            )
        try:
            data = response.json()
        except ValueError as error:
            raise MaiError(f"unreadable transcription result: {error}") from error
        if not isinstance(data, dict) or not is_response(data):
            raise MaiError(f"unexpected transcription result: {str(data)[:200]}")
        return data


class EpisodeTranscriber(Protocol):
    """The part of `MaiClient` the ASR stage uses."""

    def transcribe(
        self, audio: Path, output: Path, phrases: Sequence[str]
    ) -> TranscriptionCost: ...


# Builds the episode client from the Azure Speech credentials.
type MaiClientFactory = Callable[[AzureSpeech], EpisodeTranscriber]


def connect_mai(credentials: AzureSpeech) -> EpisodeTranscriber:
    """The real client for a whole episode."""
    return MaiClient(credentials, EPISODE_TIMEOUT_S)


def _retryable(error: MaiError) -> bool:
    # No status: the connection failed or timed out on our side.
    return error.status is None or error.status in _RETRY_STATUSES


def transcription_cost(payload: AsrPayload) -> TranscriptionCost:
    """Price the response by its `durationMilliseconds`."""
    duration = payload.get("durationMilliseconds")
    if not isinstance(duration, int | float):
        raise MaiError("MAI-Transcribe-2 result has no durationMilliseconds")
    return TranscriptionCost.priced(duration / 1000, USD_PER_AUDIO_HOUR)


def is_response(payload: AsrPayload) -> bool:
    """Whether `payload` has MAI's shape (`phrases` and `durationMilliseconds`)."""
    return "phrases" in payload and "durationMilliseconds" in payload


def read_response(path: Path) -> AsrPayload:
    """The kept `asr.json`; `AsrError` unless MAI-Transcribe-2 wrote it."""
    payload = read_payload(path)
    if not is_response(payload):
        raise AsrError(
            f"{path} is not a MAI-Transcribe-2 response (keys: "
            f"{sorted(payload)[:8]}); run `grill reset <id> --from asr` to "
            "transcribe the episode again"
        )
    return payload


def timed_words(payload: AsrPayload) -> list[WordToken]:
    """Every word of a word-timed response in response order, each with its
    phrase's speaker as `speaker_<n>`."""
    words: list[WordToken] = []
    for phrase in payload.get("phrases", []):
        if not isinstance(phrase, dict):
            continue
        speaker = phrase.get("speaker")
        speaker_id = None if speaker is None else f"speaker_{speaker}"
        for word in phrase.get("words", []):
            if not isinstance(word, dict):
                continue
            offset = word.get("offsetMilliseconds")
            duration = word.get("durationMilliseconds")
            text = str(word.get("text") or "")
            if not text or not isinstance(offset, int | float):
                continue
            length = duration if isinstance(duration, int | float) else 0
            words.append(
                WordToken(text, offset / 1000, (offset + length) / 1000, speaker_id)
            )
    return words


def srt_tokens(payload: AsrPayload) -> list[WordToken]:
    """The SRT builder's tokens: the words with a leading punctuation mark
    split off, sorted by time, listeners' back-channels inside another
    speaker's talk dropped (`drop_backchannels`), and so are the ones MAI
    gave the talker that would only flash (`drop_flashing_backchannels`)."""
    tokens = [
        part
        for word in timed_words(payload)
        for part in split_leading_mark(word, _LEADING_MARKS)
    ]
    tokens.sort(key=lambda token: (token.start, token.end))
    return drop_flashing_backchannels(drop_backchannels(tokens))


def drop_backchannels(tokens: Sequence[WordToken]) -> list[WordToken]:
    """Drop the listeners' back-channels inside one talker's talk.

    MAI gives a listener's うん its own speaker where Scribe folded it into
    the talker. Back-channel-only runs by other speakers, one or several in
    a row, that the same talker resumes after carry nothing a viewer reads:
    mid-sentence (「派手な」「うん。」「ものやった時に」) they would split the
    sentence in two blocks, between sentences (「〜企画です。」「ほー。」
    「ちなみに〜」) each became a 「-嗯」 line. They are dropped. Kept, after
    a finished sentence only: an answer to a question (the sentence ends in
    ？ or か。, but not the rhetorical 〜じゃないですか。) and a reaction
    ending in ？ (ええ？). A reply the talker
    does not resume after stays too. Kept tokens keep their times."""
    runs = [list(run) for _, run in groupby(tokens, key=lambda t: t.speaker_id)]
    out: list[WordToken] = []
    index = 0
    while index < len(runs):
        talker = out[-1] if out else None
        end = index
        while (
            talker is not None
            and end < len(runs)
            and runs[end][0].speaker_id != talker.speaker_id
            and _is_backchannel(runs[end])
        ):
            end += 1
        if (
            talker is not None
            and index < end < len(runs)
            and runs[end][0].speaker_id == talker.speaker_id
            and not _answers_or_reacts(out, runs[index:end])
        ):
            index = end
            continue
        out.extend(runs[index])
        index += 1
    return out


def drop_flashing_backchannels(tokens: Sequence[WordToken]) -> list[WordToken]:
    """Drop a back-channel sentence MAI put inside the talker's own turn
    that the next words leave no time to read.

    MAI sometimes gives the listener's へえ to the talker
    (「〜今の時代。」「へえ。」, both one speaker), so `drop_backchannels`
    cannot see it. Such a sentence, right after the same speaker's finished
    sentence, with the next word (anyone's) starting within
    `MIN_SEGMENT_DURATION_S` of it, would only flash before the next line:
    it is dropped. Kept tokens keep their times."""
    out: list[WordToken] = []
    index = 0
    while index < len(tokens):
        end = _sentence_end(tokens, index)
        sentence = tokens[index:end]
        previous = out[-1] if out else None
        if not (
            previous is not None
            and end < len(tokens)
            and previous.speaker_id == sentence[0].speaker_id
            and ends_with_hard_punctuation(previous.text)
            and _is_backchannel(sentence)
            and tokens[end].start - sentence[0].start < MIN_SEGMENT_DURATION_S
        ):
            out.extend(sentence)
        index = end
    return out


def _sentence_end(tokens: Sequence[WordToken], start: int) -> int:
    """The index after the sentence starting at `start`: through its first
    hard punctuation, or to where the speaker changes."""
    speaker = tokens[start].speaker_id
    index = start
    while index < len(tokens) and tokens[index].speaker_id == speaker:
        index += 1
        if ends_with_hard_punctuation(tokens[index - 1].text):
            break
    return index


def _answers_or_reacts(
    talk: Sequence[WordToken], listeners: Sequence[Sequence[WordToken]]
) -> bool:
    """Whether the `listeners`' back-channels after `talk` answer its finished
    question or react to its finished sentence with a ？."""
    if not ends_with_hard_punctuation(talk[-1].text):
        return False
    tail = "".join(t.text for t in talk[-_RHETORICAL_TAIL_TOKENS:])
    question = tail.endswith(("？", "?")) or (
        tail.endswith("か。") and not tail.endswith(_RHETORICAL_TAILS)
    )
    return question or any(run[-1].text.endswith(("？", "?")) for run in listeners)


def _is_backchannel(run: Sequence[WordToken]) -> bool:
    core = "".join(
        char for token in run for char in token.text if char.isalnum() or char == "ー"
    )
    return bool(core) and _BACKCHANNEL.fullmatch(core) is not None
