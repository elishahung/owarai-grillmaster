from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from grillmaster.asr.errors import AsrError
from grillmaster.asr.mai import (
    API_VERSION,
    MAX_PHRASES,
    MODEL,
    AzureSpeech,
    MaiClient,
    MaiError,
    drop_backchannels,
    drop_flashing_backchannels,
    is_response,
    read_response,
    srt_tokens,
    timed_words,
)
from grillmaster.asr.model import WordToken
from grillmaster.asr.srt_builder import build_srt_blocks

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

CREDENTIALS = AzureSpeech(endpoint="https://speech.example", key="secret")


def phrase(speaker: int, *words: tuple[str, int, int]) -> dict[str, object]:
    """A word-timed phrase; its own offset is 0, as MAI sometimes sends."""
    return {
        "speaker": speaker,
        "offsetMilliseconds": 0,
        "durationMilliseconds": 0,
        "text": "".join(text for text, _, _ in words),
        "words": [
            {"text": text, "offsetMilliseconds": start, "durationMilliseconds": length}
            for text, start, length in words
        ],
    }


def chars(text: str, start_ms: int, step_ms: int = 100) -> list[tuple[str, int, int]]:
    return [(char, start_ms + i * step_ms, step_ms) for i, char in enumerate(text)]


EPISODE_RESULT = {
    "durationMilliseconds": 3_600_000,
    "phrases": [
        phrase(0, *chars("こんにちは。", 1_000)),
        phrase(1, *chars("どうも", 2_000)),
    ],
}


def _client(handle: Callable[[httpx.Request], httpx.Response]) -> MaiClient:
    return MaiClient(CREDENTIALS, timeout_s=10.0, transport=httpx.MockTransport(handle))


def _definition(request: httpx.Request) -> dict[str, object]:
    body = request.read().decode("utf-8")
    return json.loads(body.split('name="definition"\r\n\r\n')[1].split("\r\n--")[0])


@pytest.fixture
def audio(tmp_path: Path) -> Path:
    path = tmp_path / "audio.flac"
    path.write_bytes(b"flac")
    return path


def test_an_episode_asks_for_word_times_and_keeps_the_raw_response(
    audio: Path, tmp_path: Path
):
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=EPISODE_RESULT)

    output = tmp_path / "asr.json"
    cost = _client(handle).transcribe(audio, output, ["今井らいぱち"])

    assert json.loads(output.read_text(encoding="utf-8")) == EPISODE_RESULT
    assert cost.audio_duration_s == 3600.0
    assert cost.total_usd == pytest.approx(0.10)
    (request,) = seen
    assert str(request.url) == (
        "https://speech.example/speechtotext/transcriptions:transcribe"
        f"?api-version={API_VERSION}"
    )
    assert request.headers["Ocp-Apim-Subscription-Key"] == "secret"
    assert 'name="audio"; filename="audio.flac"' in request.read().decode("utf-8")
    assert _definition(request) == {
        "locales": ["ja"],
        "enhancedMode": {
            "enabled": True,
            "model": MODEL,
            "modelOptions": {"timestamps": "word", "transcribeStyle": "verbatim"},
        },
        "diarization": {"enabled": True},
        "phraseList": {"phrases": ["今井らいぱち"]},
    }


def test_no_phrases_send_no_phrase_list(audio: Path, tmp_path: Path):
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=EPISODE_RESULT)

    _client(handle).transcribe(audio, tmp_path / "asr.json", [])

    assert "phraseList" not in _definition(seen[0])


def test_the_phrase_list_is_capped(audio: Path, tmp_path: Path):
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=EPISODE_RESULT)

    names = [f"name{i}" for i in range(MAX_PHRASES + 50)]
    _client(handle).transcribe(audio, tmp_path / "asr.json", names)

    assert _definition(seen[0])["phraseList"] == {"phrases": names[:MAX_PHRASES]}


def test_an_episode_is_tried_once_more_after_a_server_failure(
    audio: Path, tmp_path: Path
):
    replies = [httpx.Response(503, text="diarization_unavailable")]

    def handle(_: httpx.Request) -> httpx.Response:
        return replies.pop(0) if replies else httpx.Response(200, json=EPISODE_RESULT)

    _client(handle).transcribe(audio, tmp_path / "asr.json", [])

    assert replies == []


def test_an_episode_fails_after_its_second_attempt(audio: Path, tmp_path: Path):
    calls: list[int] = []

    def handle(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(408, text="The operation was timeout.")

    output = tmp_path / "asr.json"
    with pytest.raises(MaiError, match="HTTP 408"):
        _client(handle).transcribe(audio, output, [])
    assert len(calls) == 2
    assert not output.exists()


def test_a_refused_request_is_not_retried(audio: Path, tmp_path: Path):
    calls: list[int] = []

    def handle(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, text="bad key")

    with pytest.raises(MaiError, match="HTTP 401: bad key"):
        _client(handle).transcribe(audio, tmp_path / "asr.json", [])
    assert len(calls) == 1


def test_an_episode_without_words_is_not_kept(audio: Path, tmp_path: Path):
    empty = {"durationMilliseconds": 1_000, "phrases": []}
    output = tmp_path / "asr.json"
    with pytest.raises(MaiError, match="no timed words"):
        _client(lambda _: httpx.Response(200, json=empty)).transcribe(audio, output, [])
    assert not output.exists()


@pytest.mark.parametrize(
    "body", [{"phrases": []}, []], ids=["no-duration", "not-an-object"]
)
def test_an_unexpected_result_is_a_mai_error(audio: Path, tmp_path: Path, body: object):
    with pytest.raises(MaiError, match="unexpected transcription result"):
        _client(lambda _: httpx.Response(200, json=body)).transcribe(
            audio, tmp_path / "asr.json", []
        )


def test_a_response_is_recognized_by_its_phrases():
    assert is_response(EPISODE_RESULT)
    assert not is_response({"words": []})


def test_a_kept_response_of_another_shape_is_refused(tmp_path: Path):
    kept = tmp_path / "asr.json"
    kept.write_text(json.dumps(EPISODE_RESULT), encoding="utf-8")
    assert read_response(kept) == EPISODE_RESULT
    kept.write_text(json.dumps({"words": []}), encoding="utf-8")
    with pytest.raises(AsrError, match=r"not a MAI-Transcribe-2 response.*--from asr"):
        read_response(kept)


def test_words_carry_their_phrase_speaker_and_their_own_times():
    assert timed_words(EPISODE_RESULT)[:2] == [
        WordToken("こ", 1.0, 1.1, "speaker_0"),
        WordToken("ん", 1.1, 1.2, "speaker_0"),
    ]
    assert timed_words(EPISODE_RESULT)[-1] == WordToken("も", 2.2, 2.3, "speaker_1")


def test_a_leading_mark_comes_off_a_word():
    payload = {
        "durationMilliseconds": 10_000,
        "phrases": [phrase(0, ("見て", 0, 200), ("、YouTube", 300, 500))],
    }

    assert srt_tokens(payload) == [
        WordToken("見て", 0.0, 0.2, "speaker_0"),
        WordToken("、", 0.3, 0.3, "speaker_0"),
        WordToken("YouTube", 0.3, 0.8, "speaker_0"),
    ]


def tokens(*runs: tuple[str, str, float]) -> list[WordToken]:
    """One token per character, 0.1 s each, from (speaker, text, start)."""
    return [
        WordToken(char, start + i * 0.1, start + (i + 1) * 0.1, speaker)
        for speaker, text, start in runs
        for i, char in enumerate(text)
    ]


@pytest.mark.parametrize(
    "runs",
    [
        (("a", "番組とかで", 0.0), ("b", "うん。", 0.6), ("a", "会った。", 1.0)),
        (
            ("a", "あの僕の", 0.0),
            ("b", "うんうん。", 0.5),
            ("a", "系統の", 1.1),
            ("c", "ああ。", 1.5),
            ("a", "先頭。", 1.8),
        ),
        # Neither the mid-sentence か nor the ？ makes it a reply.
        (("a", "問題とか", 0.0), ("b", "ええ？", 0.5), ("a", "あって。", 0.9)),
        (("a", "企画です。", 0.0), ("b", "ほー。", 0.6), ("a", "ちなみに。", 1.0)),
        (
            ("a", "揃えました。", 0.0),
            ("b", "うん。", 0.7),
            ("c", "はいはい。", 1.0),
            ("a", "次。", 1.6),
        ),
        (("a", "もう普通", 0.0), ("b", "あ。", 0.5), ("a", "会話をしてて。", 0.8)),
        # A rhetorical じゃないですか。 seeks agreement, not an answer.
        (
            ("a", "めっちゃあるじゃないですか。", 0.0),
            ("b", "うん。", 1.5),
            ("a", "田んぼ。", 1.9),
        ),
    ],
    ids=[
        "mid-sentence",
        "cut-twice",
        "mid-sentence-reaction",
        "between-sentences",
        "several-listeners",
        "lone-あ",
        "rhetorical-じゃないですか",
    ],
)
def test_listeners_inside_the_talkers_talk_are_dropped(
    runs: tuple[tuple[str, str, float], ...],
):
    original = tokens(*runs)
    # Only the talker's tokens remain, at their times.
    assert drop_backchannels(original) == [t for t in original if t.speaker_id == "a"]


@pytest.mark.parametrize(
    "runs",
    [
        (("a", "行くんですか？", 0.0), ("b", "はい。", 0.8), ("a", "そう。", 1.2)),
        (("a", "行くんか。", 0.0), ("b", "うん。", 0.6), ("a", "そう。", 1.0)),
        (("a", "3個。", 0.0), ("b", "ええ？", 0.4), ("a", "そう。", 0.8)),
        (("a", "企画です。", 0.0), ("b", "うん。", 0.6), ("c", "次。", 1.0)),
        (("a", "あとDJさんが", 0.0), ("b", "あの裸のね。", 0.8), ("a", "出て", 1.5)),
    ],
    ids=[
        "answer-to-？",
        "answer-to-か",
        "reaction-ending-in-？",
        "no-resume",
        "more-than-a-backchannel",
    ],
)
def test_replies_and_reactions_stay(runs: tuple[tuple[str, str, float], ...]):
    original = tokens(*runs)
    assert drop_backchannels(original) == original


def test_a_talkers_own_backchannel_that_would_flash_is_dropped():
    # MAI gave the listener's へえ to the talker; the next speaker starts
    # 0.3 s after it.
    original = tokens(
        ("a", "今の時代。", 0.0), ("a", "へえ。", 0.5), ("b", "で、写真。", 0.8)
    )
    assert drop_flashing_backchannels(original) == [
        t for t in original if t.start < 0.5 or t.start >= 0.8
    ]


@pytest.mark.parametrize(
    "runs",
    [
        # Time enough to read it.
        (("a", "今の時代。", 0.0), ("a", "へえ。", 0.5), ("b", "で、写真。", 1.0)),
        # Not after the speaker's own finished sentence.
        (("a", "今の時代", 0.0), ("a", "へえ。", 0.4), ("b", "で、写真。", 0.7)),
        (("a", "今の時代。", 0.0), ("b", "へえ。", 0.5), ("c", "で、写真。", 0.8)),
    ],
    ids=["readable", "mid-sentence", "own-turn"],
)
def test_other_backchannel_sentences_stay(runs: tuple[tuple[str, str, float], ...]):
    original = tokens(*runs)
    assert drop_flashing_backchannels(original) == original


def test_a_sentence_cut_by_a_backchannel_stays_one_block():
    payload = {
        "durationMilliseconds": 10_000,
        "phrases": [
            phrase(0, *chars("番組とかで", 0)),
            phrase(1, *chars("はい。", 520)),
            phrase(0, *chars("会った。", 900)),
        ],
    }

    (block,) = build_srt_blocks(srt_tokens(payload)).blocks

    assert block.text == "番組とかで会った。"
    assert block.time_range.start == 0.0
