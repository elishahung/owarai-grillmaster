from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from grillmaster.asr.mai import (
    API_VERSION,
    MAX_PHRASES,
    MODEL,
    SRT_OPTIONS,
    AzureSpeech,
    MaiClient,
    MaiError,
    is_response,
    place_backchannels,
    read_credentials,
    srt_tokens,
    timed_words,
)
from grillmaster.asr.model import Phrase, WordToken
from grillmaster.asr.srt_builder import build_srt_blocks

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

CREDENTIALS = AzureSpeech(endpoint="https://speech.example", key="secret")
CLIP_RESULT = {
    "durationMilliseconds": 12_000,
    "combinedPhrases": [{"text": "なんでやねん。 えー。"}],
    "phrases": [
        {
            "offsetMilliseconds": 400,
            "durationMilliseconds": 900,
            "text": "なんでやねん。",
            "speaker": 1,
        },
        {
            "offsetMilliseconds": 2_250,
            "durationMilliseconds": 300,
            "text": "えー。",
            "speaker": 0,
        },
    ],
}


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


def test_a_clip_is_sent_as_verbatim_diarized_japanese(audio: Path):
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=CLIP_RESULT)

    hearing = _client(handle).hear(audio, ["今井らいぱち"])

    assert hearing.model == MODEL
    assert hearing.phrases == (
        Phrase(start_s=0.4, speaker=1, text="なんでやねん。"),
        Phrase(start_s=2.25, speaker=0, text="えー。"),
    )
    assert hearing.cost.audio_duration_s == 12.0
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
            "modelOptions": {"timestamps": "segment", "transcribeStyle": "verbatim"},
        },
        "diarization": {"enabled": True},
        "phraseList": {"phrases": ["今井らいぱち"]},
    }


def test_an_episode_asks_for_word_times_and_keeps_the_raw_response(
    audio: Path, tmp_path: Path
):
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=EPISODE_RESULT)

    output = tmp_path / "asr.json"
    cost = _client(handle).transcribe(audio, output, [])

    assert json.loads(output.read_text(encoding="utf-8")) == EPISODE_RESULT
    assert cost.audio_duration_s == 3600.0
    assert cost.total_usd == pytest.approx(0.10)
    definition = _definition(seen[0])
    assert definition["enhancedMode"]["modelOptions"]["timestamps"] == "word"  # pyright: ignore[reportIndexIssue]
    assert "phraseList" not in definition


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
    "body",
    [
        {"phrases": []},
        {
            "durationMilliseconds": 1,
            "phrases": [{"offsetMilliseconds": 0, "text": None}],
        },
        [],
    ],
    ids=["no-duration", "null-text", "not-an-object"],
)
def test_an_unexpected_result_is_a_mai_error(audio: Path, body: object):
    with pytest.raises(MaiError, match="unexpected transcription result"):
        _client(lambda _: httpx.Response(200, json=body)).hear(audio, [])


def test_the_credentials_come_from_the_env_file_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env_file = tmp_path / ".env"
    monkeypatch.setenv("AZURE_SPEECH_KEY", "from-env")
    env_file.write_text(
        "AZURE_SPEECH_ENDPOINT=https://speech.example/\n", encoding="utf-8"
    )
    with pytest.raises(MaiError, match="AZURE_SPEECH_KEY not set"):
        read_credentials(env_file)
    env_file.write_text(
        "AZURE_SPEECH_KEY=from-file\nAZURE_SPEECH_ENDPOINT=https://speech.example/\n",
        encoding="utf-8",
    )
    assert read_credentials(env_file) == AzureSpeech(
        endpoint="https://speech.example", key="from-file"
    )


def test_a_response_is_recognized_by_its_phrases():
    assert is_response(EPISODE_RESULT)
    assert not is_response({"words": []})


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


def text_of(result: list[WordToken]) -> list[tuple[str | None, str]]:
    return [
        (token.speaker_id, "".join(t.text for t in group))
        for token, group in _runs(result)
    ]


def _runs(result: list[WordToken]) -> list[tuple[WordToken, list[WordToken]]]:
    runs: list[tuple[WordToken, list[WordToken]]] = []
    for token in result:
        if runs and runs[-1][0].speaker_id == token.speaker_id:
            runs[-1][1].append(token)
        else:
            runs.append((token, [token]))
    return runs


def test_a_backchannel_in_a_one_line_sentence_moves_behind_it():
    original = tokens(
        ("a", "番組とかで", 0.0), ("b", "うん。", 0.6), ("a", "会った。", 1.0)
    )

    result = place_backchannels(original, 24)

    assert text_of(result) == [("a", "番組とかで会った。"), ("b", "うん。")]
    # Only the order changes.
    assert sorted(result, key=id) == sorted(original, key=id)


def test_a_backchannel_in_a_two_line_sentence_is_dropped():
    long_head = "だからゴールデンボンバーが本当に派手な"
    result = place_backchannels(
        tokens(
            ("a", long_head, 0.0), ("b", "うん。", 2.0), ("a", "ものやった時に。", 2.5)
        ),
        24,
    )

    assert text_of(result) == [("a", f"{long_head}ものやった時に。")]


def test_a_sentence_cut_twice_drops_both_backchannels():
    result = place_backchannels(
        tokens(
            ("a", "あの僕の", 0.0),
            ("b", "うんうん。", 0.5),
            ("a", "系統の", 1.1),
            ("c", "ああ。", 1.5),
            ("a", "先頭。", 1.8),
        ),
        24,
    )

    assert text_of(result) == [("a", "あの僕の系統の先頭。")]


def test_a_moved_backchannel_never_stands_in_for_the_talker():
    # B's うん (no mark) moves behind A's line; D's うん then follows B's
    # next line, which is not cut into, so D's stays where it is.
    result = place_backchannels(
        tokens(
            ("a", "番組とかで", 0.0),
            ("b", "うん", 0.6),
            ("a", "会った。", 0.9),
            ("d", "うん", 1.4),
            ("b", "そう", 1.7),
        ),
        24,
    )

    assert text_of(result) == [
        ("a", "番組とかで会った。"),
        ("b", "うん"),
        ("d", "うん"),
        ("b", "そう"),
    ]


@pytest.mark.parametrize(
    "runs",
    [
        # A reply after a finished sentence.
        (("a", "行くんですか？", 0.0), ("b", "はい。", 0.8), ("a", "そう。", 1.2)),
        # More than back-channel words.
        (("a", "あとDJさんが", 0.0), ("b", "あの裸のね。", 0.8), ("a", "出て", 1.5)),
        # Nobody resumes.
        (("a", "それで", 0.0), ("b", "うん。", 0.4), ("c", "次。", 0.8)),
    ],
    ids=["after-a-sentence", "more-than-a-backchannel", "no-resume"],
)
def test_other_turns_stay_where_they_are(runs: tuple[tuple[str, str, float], ...]):
    original = tokens(*runs)
    assert place_backchannels(original, 24) == original


def test_a_moved_backchannel_becomes_the_sentences_second_line():
    payload = {
        "durationMilliseconds": 10_000,
        "phrases": [
            phrase(0, *chars("番組とかで", 0)),
            phrase(1, *chars("はい。", 520)),
            phrase(0, *chars("会った。", 900)),
        ],
    }

    (block,) = build_srt_blocks(srt_tokens(payload), SRT_OPTIONS).blocks

    assert block.text == "-番組とかで会った。\n-はい。"
    assert block.time_range.start == 0.0
