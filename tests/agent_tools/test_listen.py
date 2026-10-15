from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import TextContent
from tests.fakes import FakeAgentRunner

from grillmaster.agent_tools.listen import Listener
from grillmaster.agent_tools.server import build_server
from grillmaster.agents.errors import AgentOutputError
from grillmaster.core.model_spec import Role
from grillmaster.core.tool_session import ListenTool, ToolSession

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fakes import FakeFfmpeg

TASK = "listen/0010.000-0040.500"
SPAN_DIR = "0010.000-0040.500"


@pytest.fixture
def config(tmp_path: Path) -> ListenTool:
    """Spans of up to 60 s, from 5 s to the end of the 100 s audio."""
    return ListenTool(
        audio=tmp_path / "work" / "05_audio" / "audio.ogg",
        clips_dir=tmp_path / "work" / "12_proofread" / "listen",
        model="gemini/gemini-3.8-flash/low",
        timeout_s=60.0,
        window=(5.0, None),
        max_span_s=60.0,
    )


def test_a_span_is_cut_transcribed_and_kept(
    config: ListenTool, fake_ffmpeg: FakeFfmpeg
):
    agents = FakeAgentRunner({TASK: "  えー、今井らいぱちや！\n"})
    listener = Listener(config, fake_ffmpeg, agents)

    text = listener.transcribe(10.0, 40.5)

    assert text == "Transcript of 10.000s-40.500s:\nえー、今井らいぱちや！"
    task = agents.task(TASK)
    clip_dir = config.clips_dir / SPAN_DIR
    assert task.role is Role.LISTEN
    assert task.audio == (clip_dir / "clip.ogg",)
    assert task.workdir == clip_dir
    assert (clip_dir / "clip.ogg").is_file()
    assert (clip_dir / "transcript.txt").read_text(encoding="utf-8") == (
        "えー、今井らいぱちや！\n"
    )
    # The kept transcript answers again without a new session or cut.
    calls = len(fake_ffmpeg.calls)
    assert listener.transcribe(10.0, 40.5) == text
    assert len(agents.tasks) == 1
    assert len(fake_ffmpeg.calls) == calls


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (20.0, 20.0, "end must be after start"),
        (1.0, 30.0, "outside the allowed window"),
        (90.0, 101.0, "outside the allowed window"),
        (10.0, 80.0, "at most 60s per call"),
    ],
)
def test_bad_spans_are_tool_errors(
    config: ListenTool,
    fake_ffmpeg: FakeFfmpeg,
    start: float,
    end: float,
    message: str,
):
    agents = FakeAgentRunner()
    with pytest.raises(ToolError, match=message):
        Listener(config, fake_ffmpeg, agents).transcribe(start, end)
    assert agents.tasks == []


def test_a_failed_transcription_is_a_tool_error(
    config: ListenTool, fake_ffmpeg: FakeFfmpeg
):
    agents = FakeAgentRunner({TASK: AgentOutputError("empty transcript")})
    with pytest.raises(ToolError, match="transcription failed: empty transcript"):
        Listener(config, fake_ffmpeg, agents).transcribe(10.0, 40.5)
    assert not (config.clips_dir / SPAN_DIR / "transcript.txt").exists()


def test_the_server_serves_listen(
    session: ToolSession, config: ListenTool, fake_ffmpeg: FakeFfmpeg
):
    agents = FakeAgentRunner({TASK: "なんでやねん"})
    server = build_server(
        session.model_copy(update={"listen": config}), fake_ffmpeg, agents
    )

    async def call() -> tuple[set[str], list[str]]:
        names = {tool.name for tool in await server.list_tools()}
        content = await server.call_tool("listen", {"start": 10.0, "end": 40.5})
        texts = [item.text for item in content if isinstance(item, TextContent)]
        return names, texts

    names, texts = anyio.run(call)
    assert names == {"get_frames", "check_srt", "listen"}
    assert texts == ["Transcript of 10.000s-40.500s:\nなんでやねん"]
