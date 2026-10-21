from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, get_args

import anyio
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import TextContent

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
    from collections.abc import Callable
    from pathlib import Path

    from tests.fakes import FakeFfmpeg

SPAN_DIR = "0010.000-0040.500"
USAGE = Usage(
    input_tokens=17_000,
    cached_input_tokens=1_000,
    output_tokens=5_000,
    reasoning_tokens=4_000,
)
ANSWER = "## 発話\n[1.5] 大悟: 64歳でした\n\n## テロップ\n[0.0] 石塚英彦(64)\n\n## 場面\n楽屋。"
SHIFTED = "## 発話\n[11.5] 大悟: 64歳でした\n\n## テロップ\n[10.0] 石塚英彦(64)\n\n## 場面\n楽屋。"


class FakeModel:
    """Answers every clip with `reply` (an `Answer`, an error, or a callable)."""

    def __init__(self, reply: Answer | Exception | Callable[[], Answer]) -> None:
        self.reply = reply
        self.clips: list[Path] = []

    def ask(self, instructions: str, video: Path) -> Answer:
        assert "## テロップ" in instructions
        self.clips.append(video)
        if isinstance(self.reply, Exception):
            raise self.reply
        if callable(self.reply):
            return self.reply()
        return self.reply


@pytest.fixture
def config(tmp_path: Path) -> WatchClipTool:
    """Spans of up to 60 s anywhere in the 100 s video."""
    return WatchClipTool(
        video=tmp_path / "video.mp4",
        clips_dir=tmp_path / "work" / "12_proofread" / "watch_clip",
        model="muse-spark-1.3-contributor",
        effort="high",
        env_file=tmp_path / ".env",
        timeout_s=60.0,
        max_span_s=60.0,
    )


def test_a_span_is_cut_watched_priced_and_kept(
    config: WatchClipTool, fake_ffmpeg: FakeFfmpeg
):
    model = FakeModel(Answer(ANSWER, USAGE))
    watcher = ClipWatcher(config, fake_ffmpeg, model)

    text = watcher.watch(10.0, 40.5)

    assert text == (
        "Clip 10.000s-40.500s (times are video seconds, within about 2 s):\n" + SHIFTED
    )
    clip_dir = config.clips_dir / SPAN_DIR
    assert model.clips == [clip_dir / "clip.mp4"]
    assert (clip_dir / "clip.mp4").is_file()
    cost = USAGE.cost_usd(PRICES[config.model])
    assert cost_since(config.clips_dir, frozenset()) == (1, cost)
    # The kept record answers again without a new request or cut.
    calls = len(fake_ffmpeg.calls)
    assert watcher.watch(10.0, 40.5) == text
    assert len(model.clips) == 1
    assert len(fake_ffmpeg.calls) == calls


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
    with pytest.raises(ToolError, match=message):
        ClipWatcher(config, fake_ffmpeg, model).watch(start, end)
    assert model.clips == []


def test_a_failed_request_is_a_tool_error(
    config: WatchClipTool, fake_ffmpeg: FakeFfmpeg
):
    model = FakeModel(MetaError("RateLimitError: slow down"))
    with pytest.raises(ToolError, match="watching the clip failed: RateLimitError"):
        ClipWatcher(config, fake_ffmpeg, model).watch(10.0, 40.5)
    assert cost_since(config.clips_dir, frozenset()) == (0, 0.0)


def test_a_billed_request_without_an_answer_still_counts(
    config: WatchClipTool, fake_ffmpeg: FakeFfmpeg
):
    model = FakeModel(MetaError("ended incomplete", usage=USAGE))
    watcher = ClipWatcher(config, fake_ffmpeg, model)
    for _ in range(2):
        with pytest.raises(ToolError, match="ended incomplete"):
            watcher.watch(10.0, 40.5)
    cost = USAGE.cost_usd(PRICES[config.model])
    assert cost_since(config.clips_dir, frozenset()) == (2, pytest.approx(2 * cost))
    # No answer was kept, so the span is asked again.
    model.reply = Answer(ANSWER, USAGE)
    watcher.watch(10.0, 40.5)
    assert len(model.clips) == 3


def test_the_server_serves_watch_clip(
    session: ToolSession, config: WatchClipTool, fake_ffmpeg: FakeFfmpeg
):
    model = FakeModel(Answer(ANSWER, USAGE))
    server = build_server(
        session.model_copy(update={"watch_clip": config}), fake_ffmpeg, model
    )

    async def call() -> tuple[set[str], list[str]]:
        names = {tool.name for tool in await server.list_tools()}
        content = await server.call_tool("watch_clip", {"start": 10.0, "end": 40.5})
        texts = [item.text for item in content if isinstance(item, TextContent)]
        return names, texts

    names, texts = anyio.run(call)
    assert names == {"get_frames", "check_srt", "watch_clip"}
    assert texts == [
        "Clip 10.000s-40.500s (times are video seconds, within about 2 s):\n" + SHIFTED
    ]


def test_a_repeated_span_waits_for_the_first_record(
    config: WatchClipTool, fake_ffmpeg: FakeFfmpeg
):
    started = threading.Event()
    release = threading.Event()

    def slow() -> Answer:
        started.set()
        assert release.wait(5)
        return Answer("一回だけ", USAGE)

    model = FakeModel(slow)
    watcher = ClipWatcher(config, fake_ffmpeg, model)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(watcher.watch, 10.0, 40.5)
        assert started.wait(5)
        second = pool.submit(watcher.watch, 10.0, 40.5)
        release.set()
        results = {first.result(5), second.result(5)}
    assert len(results) == 1
    assert len(model.clips) == 1


def test_the_key_comes_from_the_env_file_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    env_file = tmp_path / ".env"
    monkeypatch.setenv("META_API_KEY", "from-env")
    with pytest.raises(MetaError, match="META_API_KEY is not set"):
        read_api_key(env_file)
    env_file.write_text("META_API_KEY=from-file\n", encoding="utf-8")
    assert read_api_key(env_file) == "from-file"


def test_every_muse_model_has_a_price():
    assert set(PRICES) == set(get_args(MuseModel))
