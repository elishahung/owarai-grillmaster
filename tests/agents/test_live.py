"""Live checks against the real agent CLIs (`pytest -m live`; spends quota).

One task per backend covers the schema answer, an MCP tool yielding an
image (returned, or attached to the next message), and a repair round that
resumes the session. With `GRILL_RECORD_FIXTURES=1` (see
`scripts/record_agent_fixture.py`) the raw streams are saved per turn as
`tests/fixtures/agents/<backend>/<name>.jsonl`, named by `LIVE_TURNS`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel
from tests.agents.fakes import FIXTURES, LIVE_TURNS, SPECS

from grillmaster.agents.adapters import AdapterRegistry
from grillmaster.agents.errors import ValidationFailure
from grillmaster.agents.runner import AgentRunner
from grillmaster.agents.task import AgentTask, SchemaOutput
from grillmaster.core.model_spec import Backend, Role
from grillmaster.core.tool_session import FramesTool, ToolSession
from grillmaster.events.types import ActivityKind, AgentActivity

if TYPE_CHECKING:
    from tests.fakes import RecordingSink

pytestmark = pytest.mark.live

RECORD_ENV_VAR = "GRILL_RECORD_FIXTURES"
TOOL_SERVER = (sys.executable, str(Path(__file__).with_name("live_tool_server.py")))
# A record that opens each turn's stream, per backend. Gemini's one ACP
# stream has none: its turns end with their prompt's response instead.
TURN_MARKERS = {
    Backend.AGY: ('"event"', '"init"'),
    Backend.CODEX: ('"type"', '"thread.started"'),
    Backend.CLAUDE: ('"SystemMessage"', '"init"'),
}
TURN_END_MARKER = '"stopReason"'


class LiveAnswer(BaseModel):
    color: str
    word: str


def require_uppercase_word(answer: LiveAnswer) -> None:
    if answer.word != answer.word.upper():
        raise ValidationFailure(f"word must be UPPERCASE, got {answer.word!r}")


@pytest.mark.parametrize("backend", list(Backend))
def test_schema_mcp_image_and_resume(
    backend: Backend, tmp_path: Path, recording_sink: RecordingSink
):
    runner = AgentRunner(
        {Role.UTILITY: SPECS[backend]},
        AdapterRegistry(),
        max_concurrent=1,
        timeout_s=600,
        events=recording_sink,
        tool_server=TOOL_SERVER,
    )
    tools = ToolSession(
        project_root=tmp_path,
        frames=FramesTool(
            video=tmp_path / "video.mp4",
            frames_dir=tmp_path / "frames",
            window=(0.0, None),
            max_side=64,
        ),
        check_srt=None,
    )
    task = AgentTask(
        name=f"live/{backend}",
        role=Role.UTILITY,
        instructions="This is a short connectivity test; follow it literally.",
        prompt=(
            "Call the get_frames tool once with times [1.0]. Set color to the "
            "dominant color of the returned frame as one lowercase English word, "
            "and set word to the English word hello in lowercase."
        ),
        session_dir=tmp_path / "session",
        workdir=tmp_path / "work",
        output=SchemaOutput(LiveAnswer),
        tools=tools,
        validate=require_uppercase_word,
        max_repairs=1,
    )
    result = runner.run(task)

    if os.environ.get(RECORD_ENV_VAR):
        _record(backend, result.session_dir / "raw.jsonl")
    assert result.output.color.lower() == "green"
    assert result.output.word == "HELLO"
    assert result.repairs == 1
    assert any(
        isinstance(event, AgentActivity)
        and event.kind is ActivityKind.TOOL_CALL
        and event.summary.startswith("get_frames")
        for event in recording_sink.events
    )


def _record(backend: Backend, raw: Path) -> None:
    lines = raw.read_text(encoding="utf-8").splitlines()
    turns = (
        _split_after_ends(lines)
        if backend is Backend.GEMINI
        else _split_at_starts(lines, TURN_MARKERS[backend])
    )
    target = FIXTURES / backend
    target.mkdir(exist_ok=True)
    for name, lines in zip(LIVE_TURNS[backend], turns, strict=True):
        json.loads(lines[0])  # fail loudly on a garbled recording
        (target / f"{name}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _split_at_starts(lines: list[str], markers: tuple[str, ...]) -> list[list[str]]:
    """Turns that each open with a line holding every marker."""
    turns: list[list[str]] = []
    for line in lines:
        if all(marker in line for marker in markers) or not turns:
            turns.append([])
        turns[-1].append(line)
    return turns


def _split_after_ends(lines: list[str]) -> list[list[str]]:
    """Turns that each end with their prompt's response."""
    turns: list[list[str]] = [[]]
    for line in lines:
        turns[-1].append(line)
        if TURN_END_MARKER in line:
            turns.append([])
    return [turn for turn in turns if turn]
