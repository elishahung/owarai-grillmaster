from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, get_args

import anyio
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import TextContent

from grillmaster.agent_tools.mai import MaiError, Phrase, Transcript
from grillmaster.agent_tools.meta import (
    PRICES,
    Answer,
    MetaError,
    Price,
    Usage,
    read_api_key,
)
from grillmaster.agent_tools.server import build_server
from grillmaster.agent_tools.watch_clip import ClipWatcher, shift_times
from grillmaster.agent_tools.watch_record import cost_since
from grillmaster.core.tool_session import MuseModel, ToolSession, WatchClipTool

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from tests.fakes import FakeFfmpeg

SPAN_DIR = "0010.000-0040.500"
USAGE = Usage(
    input_tokens=17_000,
    cached_input_tokens=1_000,
    output_tokens=5_000,
    reasoning_tokens=4_000,
)
ANSWER = "## テロップ\n[0.0] 石塚英彦(64)\n\n## 場面\n楽屋。"
SHIFTED = "## テロップ\n[10.0] 石塚英彦(64)\n\n## 場面\n楽屋。"
TRANSCRIPT = Transcript(
    phrases=(
        Phrase(start_s=1.5, speaker=0, text="64歳でした。"),
        Phrase(start_s=3.25, speaker=1, text="えー。"),
    ),
    audio_s=30.5,
)
SPEECH = "## 発話\n\n[11.5] 話者0: 64歳でした。\n[13.2] 話者1: えー。"
HEADER = "Clip 10.000s-40.500s (times are video seconds; テロップ within about 2 s):\n"


class FakeModel:
    """Answers every clip with `reply` (an `Answer`, an error, or a callable)."""

    def __init__(self, reply: Answer | Exception | Callable[[], Answer]) -> None:
        self.reply = reply
        self.clips: list[Path] = []

    def ask(self, instructions: str, video: Path) -> Answer:
        assert "## テロップ" in instructions
        assert "## 発話" not in instructions
        self.clips.append(video)
        if isinstance(self.reply, Exception):
            raise self.reply
        if callable(self.reply):
            return self.reply()
        return self.reply


class FakeSpeech:
    """Transcribes every clip's audio as `reply` (a `Transcript` or an error)."""

    def __init__(self, reply: Transcript | Exception = TRANSCRIPT) -> None:
        self.reply = reply
        self.calls: list[tuple[Path, tuple[str, ...]]] = []

    def transcribe(self, audio: Path, phrases: Sequence[str]) -> Transcript:
        self.calls.append((audio, tuple(phrases)))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.fixture
def config(tmp_path: Path) -> WatchClipTool:
    """Spans of up to 60 s anywhere in the 100 s video."""
    return WatchClipTool(
        video=tmp_path / "video.mp4",
        clips_dir=tmp_path / "work" / "12_proofread" / "watch_clip",
        model="muse-spark-1.3-contributor",
        effort="high",
        speech_phrases=("石塚英彦",),
        env_file=tmp_path / ".env",
        timeout_s=60.0,
        max_span_s=60.0,
    )


def test_a_span_is_heard_watched_priced_and_kept(
    config: WatchClipTool, fake_ffmpeg: FakeFfmpeg
):
    model = FakeModel(Answer(ANSWER, USAGE))
    speech = FakeSpeech()
    watcher = ClipWatcher(config, fake_ffmpeg, model, speech)

    text = watcher.watch(10.0, 40.5)

    assert text == f"{HEADER}{SPEECH}\n\n{SHIFTED}"
    clip_dir = config.clips_dir / SPAN_DIR
    assert model.clips == [clip_dir / "clip.mp4"]
    assert speech.calls == [(clip_dir / "audio.flac", ("石塚英彦",))]
    assert (clip_dir / "clip.mp4").is_file()
    assert (clip_dir / "audio.flac").is_file()
    cost = USAGE.cost_usd(PRICES[config.model]) + TRANSCRIPT.cost_usd
    assert cost_since(config.clips_dir, frozenset()) == (2, pytest.approx(cost))
    # The kept records answer again without a new request or cut.
    calls = len(fake_ffmpeg.calls)
    assert watcher.watch(10.0, 40.5) == text
    assert len(model.clips) == 1
    assert len(speech.calls) == 1
    assert len(fake_ffmpeg.calls) == calls


def test_speech_is_priced_by_audio_length():
    assert Transcript((), audio_s=3600.0).cost_usd == pytest.approx(0.10)


def test_usage_is_priced_with_cached_input_at_its_own_rate():
    price = Price(input=0.10, cached_input=0.002, output=0.20)
    assert USAGE.cost_usd(price) == pytest.approx(
        (16_000 * 0.10 + 1_000 * 0.002 + 5_000 * 0.20) / 1_000_000
    )


def test_only_leading_times_shift():
    assert shift_times("[2]: [3] は\n 発話 [4.25]\n[0.04] x", 100.0) == (
        "[102.0]: [3] は\n 発話 [4.25]\n[100.0] x"
    )


def test_the_slipped_time_forms_shift_too():
    text = "[3.2s] a\n- [1:02.5] b\n  [4] c"
    assert shift_times(text, 100.0) == "[103.2] a\n- [162.5] b\n  [104.0] c"


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (20.0, 20.0, "end must be after start"),
        (10.0001, 10.0004, "end must be after start"),
        (-1.0, 30.0, "outside the video"),
        (90.0, 101.0, "outside the video"),
        (10.0, 80.0, "at most 60s per call"),
    ],
)
def test_bad_spans_are_tool_errors(
    config: WatchClipTool,
    fake_ffmpeg: FakeFfmpeg,
    start: float,
    end: float,
    message: str,
):
    model = FakeModel(Answer(ANSWER, USAGE))
    speech = FakeSpeech()
    with pytest.raises(ToolError, match=message):
        ClipWatcher(config, fake_ffmpeg, model, speech).watch(start, end)
    assert model.clips == []
    assert speech.calls == []


def test_a_failed_hearing_still_answers_with_the_captions(
    config: WatchClipTool, fake_ffmpeg: FakeFfmpeg
):
    model = FakeModel(Answer(ANSWER, USAGE))
    speech = FakeSpeech(MaiError("HTTP 429: slow down"))
    watcher = ClipWatcher(config, fake_ffmpeg, model, speech)

    text = watcher.watch(10.0, 40.5)

    assert text == (
        f"{HEADER}## 発話\n\n（取得できず: hearing the clip failed: HTTP 429: "
        f"slow down）\n\n{SHIFTED}"
    )
    assert cost_since(config.clips_dir, frozenset()) == (
        1,
        pytest.approx(USAGE.cost_usd(PRICES[config.model])),
    )
    # The captions are kept; only the speech is asked again.
    speech.reply = TRANSCRIPT
    assert watcher.watch(10.0, 40.5) == f"{HEADER}{SPEECH}\n\n{SHIFTED}"
    assert len(model.clips) == 1


def test_a_failed_watch_still_answers_with_the_speech(
    config: WatchClipTool, fake_ffmpeg: FakeFfmpeg
):
    model = FakeModel(MetaError("RateLimitError: slow down"))
    watcher = ClipWatcher(config, fake_ffmpeg, model, FakeSpeech())

    text = watcher.watch(10.0, 40.5)

    assert text.startswith(f"{HEADER}{SPEECH}\n\n## テロップ\n\n（取得できず: ")
    assert "RateLimitError" in text
    assert cost_since(config.clips_dir, frozenset()) == (
        1,
        pytest.approx(TRANSCRIPT.cost_usd),
    )
    # The speech is kept; only the picture is asked again.
    model.reply = Answer(ANSWER, USAGE)
    assert watcher.watch(10.0, 40.5) == f"{HEADER}{SPEECH}\n\n{SHIFTED}"


def test_a_billed_request_without_an_answer_still_counts(
    config: WatchClipTool, fake_ffmpeg: FakeFfmpeg
):
    model = FakeModel(MetaError("ended incomplete", usage=USAGE))
    watcher = ClipWatcher(config, fake_ffmpeg, model, FakeSpeech())
    for _ in range(2):
        assert "ended incomplete" in watcher.watch(10.0, 40.5)
    cost = 2 * USAGE.cost_usd(PRICES[config.model]) + TRANSCRIPT.cost_usd
    assert cost_since(config.clips_dir, frozenset()) == (3, pytest.approx(cost))
    # No answer was kept, so the span is watched again.
    model.reply = Answer(ANSWER, USAGE)
    watcher.watch(10.0, 40.5)
    assert len(model.clips) == 3


def test_the_server_serves_watch_clip(
    session: ToolSession, config: WatchClipTool, fake_ffmpeg: FakeFfmpeg
):
    model = FakeModel(Answer(ANSWER, USAGE))
    server = build_server(
        session.model_copy(update={"watch_clip": config}),
        fake_ffmpeg,
        model,
        FakeSpeech(),
    )

    async def call() -> tuple[set[str], list[str]]:
        names = {tool.name for tool in await server.list_tools()}
        content = await server.call_tool("watch_clip", {"start": 10.0, "end": 40.5})
        texts = [item.text for item in content if isinstance(item, TextContent)]
        return names, texts

    names, texts = anyio.run(call)
    assert names == {"get_frames", "check_srt", "watch_clip"}
    assert texts == [f"{HEADER}{SPEECH}\n\n{SHIFTED}"]


def test_a_repeated_span_waits_for_the_first_records(
    config: WatchClipTool, fake_ffmpeg: FakeFfmpeg
):
    started = threading.Event()
    release = threading.Event()

    def slow() -> Answer:
        started.set()
        assert release.wait(5)
        return Answer("一回だけ", USAGE)

    model = FakeModel(slow)
    speech = FakeSpeech()
    watcher = ClipWatcher(config, fake_ffmpeg, model, speech)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(watcher.watch, 10.0, 40.5)
        assert started.wait(5)
        second = pool.submit(watcher.watch, 10.0, 40.5)
        release.set()
        results = {first.result(5), second.result(5)}
    assert len(results) == 1
    assert len(model.clips) == 1
    assert len(speech.calls) == 1


def test_the_key_comes_from_the_env_file_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env_file = tmp_path / ".env"
    monkeypatch.setenv("META_API_KEY", "from-env")
    with pytest.raises(MetaError, match="META_API_KEY not set in"):
        read_api_key(env_file)
    env_file.write_text("META_API_KEY=from-file\n", encoding="utf-8")
    assert read_api_key(env_file) == "from-file"


def test_every_muse_model_has_a_price():
    assert set(PRICES) == set(get_args(MuseModel))
