from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import anyio
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import TextContent
from tests.fakes import FakeAgentRunner

from grillmaster.agent_tools.server import build_server
from grillmaster.agent_tools.transcribe import Transcriber, _task
from grillmaster.agents.adapters.base import Media, MediaKind, ToolImageDelivery
from grillmaster.agents.errors import AgentOutputError
from grillmaster.agents.runner import TRANSCRIBE_REQUIRES, _required_capabilities
from grillmaster.core.model_spec import ModelSpec, Role
from grillmaster.core.timecode import TimeRange
from grillmaster.core.tool_session import ToolSession, TranscribeTool

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fakes import FakeFfmpeg

TASK = "transcribe/0010.000-0040.500"
SPAN_DIR = "0010.000-0040.500"


@pytest.fixture
def config(tmp_path: Path) -> TranscribeTool:
    """Spans of up to 60 s anywhere in the 100 s video."""
    return TranscribeTool(
        video=tmp_path / "video.mp4",
        clips_dir=tmp_path / "work" / "12_proofread" / "transcribe",
        model=ModelSpec.parse("gemini/gemini-3.8-flash/low"),
        timeout_s=60.0,
        max_span_s=60.0,
    )


def test_a_span_is_cut_transcribed_and_kept(
    config: TranscribeTool, fake_ffmpeg: FakeFfmpeg
):
    agents = FakeAgentRunner({TASK: "  えー、今井らいぱちや！\n"})
    transcriber = Transcriber(config, fake_ffmpeg, agents)

    text = transcriber.transcribe(10.0, 40.5)

    assert text == "Transcript of 10.000s-40.500s:\nえー、今井らいぱちや！"
    task = agents.task(TASK)
    clip_dir = config.clips_dir / SPAN_DIR
    assert task.role is Role.TRANSCRIBE
    assert task.media == (Media(MediaKind.VIDEO, clip_dir / "clip.mp4"),)
    assert not task.builtin_tools
    # The session's cwd holds nothing to process instead of watching it.
    assert task.workdir == clip_dir / "work"
    assert (clip_dir / "clip.mp4").is_file()
    assert (clip_dir / "transcript.txt").read_text(encoding="utf-8") == (
        "えー、今井らいぱちや！\n"
    )
    # The kept transcript answers again without a new session or cut.
    calls = len(fake_ffmpeg.calls)
    assert transcriber.transcribe(10.0, 40.5) == text
    assert len(agents.tasks) == 1
    assert len(fake_ffmpeg.calls) == calls


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
    config: TranscribeTool,
    fake_ffmpeg: FakeFfmpeg,
    start: float,
    end: float,
    message: str,
):
    agents = FakeAgentRunner()
    with pytest.raises(ToolError, match=message):
        Transcriber(config, fake_ffmpeg, agents).transcribe(start, end)
    assert agents.tasks == []


def test_a_failed_transcription_is_a_tool_error(
    config: TranscribeTool, fake_ffmpeg: FakeFfmpeg
):
    agents = FakeAgentRunner({TASK: AgentOutputError("empty transcript")})
    with pytest.raises(ToolError, match="transcription failed: empty transcript"):
        Transcriber(config, fake_ffmpeg, agents).transcribe(10.0, 40.5)
    assert not (config.clips_dir / SPAN_DIR / "transcript.txt").exists()


def test_the_server_serves_transcribe(
    session: ToolSession, config: TranscribeTool, fake_ffmpeg: FakeFfmpeg
):
    agents = FakeAgentRunner({TASK: "なんでやねん"})
    server = build_server(
        session.model_copy(update={"transcribe": config}), fake_ffmpeg, agents
    )

    async def call() -> tuple[set[str], list[str]]:
        names = {tool.name for tool in await server.list_tools()}
        content = await server.call_tool("transcribe", {"start": 10.0, "end": 40.5})
        texts = [item.text for item in content if isinstance(item, TextContent)]
        return names, texts

    names, texts = anyio.run(call)
    assert names == {"get_frames", "check_srt", "transcribe"}
    assert texts == ["Transcript of 10.000s-40.500s:\nなんでやねん"]


def test_a_repeated_span_waits_for_the_first_transcript(
    config: TranscribeTool, fake_ffmpeg: FakeFfmpeg
):
    started = threading.Event()
    release = threading.Event()

    def slow(task: object) -> str:
        started.set()
        assert release.wait(5)
        return "一回だけ"

    agents = FakeAgentRunner({TASK: slow})
    transcriber = Transcriber(config, fake_ffmpeg, agents)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(transcriber.transcribe, 10.0, 40.5)
        assert started.wait(5)
        second = pool.submit(transcriber.transcribe, 10.0, 40.5)
        release.set()
        results = {first.result(5), second.result(5)}
    assert results == {"Transcript of 10.000s-40.500s:\n一回だけ"}
    assert len(agents.tasks) == 1


def test_the_runner_checks_what_the_transcribe_session_needs(config: TranscribeTool):
    task = _task(TimeRange(10.0, 40.5), config.clips_dir / "c.mp4", config.clips_dir)
    assert _required_capabilities(task, ToolImageDelivery.INLINE) == TRANSCRIBE_REQUIRES
