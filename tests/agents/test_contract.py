"""Adapter contract: recorded CLI streams replayed through each adapter.

The fixtures under `tests/fixtures/agents/` are real recordings (agy 1.3.2,
codex-cli 0.160.0, Claude Code via claude-agent-sdk 0.2.135). Re-record them
with `scripts/record_agent_fixture.py` after a CLI upgrade.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from tests.agents.fakes import (
    SPECS,
    FakeProcess,
    FakeQuery,
    FakeSpawn,
    claude_messages,
    fixture_lines,
)

from grillmaster.agents.adapters.agy import AgyAdapter
from grillmaster.agents.adapters.claude import ClaudeAdapter
from grillmaster.agents.adapters.codex import CodexAdapter
from grillmaster.agents.errors import (
    AgentConfigError,
    AgentError,
    AgentQuotaError,
    AgentTransientError,
)
from grillmaster.agents.events import Message, ToolCall, ToolResult
from grillmaster.core.model_spec import Backend

if TYPE_CHECKING:
    from collections.abc import Callable

    from grillmaster.agents.adapters.base import (
        AgentAdapter,
        FinalOutput,
        TurnRequest,
    )
    from grillmaster.agents.events import AgentEvent

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}
AGY_MODEL_ID = f"{SPECS[Backend.AGY].model}-low"


@dataclass
class Replay:
    adapter: AgentAdapter
    spawn: FakeSpawn | None = None
    query: FakeQuery | None = None
    events: list[AgentEvent] = field(default_factory=list)
    final: FinalOutput | None = None
    error: AgentError | None = None


def replay(
    backend: Backend,
    request: TurnRequest,
    *,
    fixture: str | None = None,
    lines: list[str] | None = None,
    messages: list[Any] | None = None,
    resume: str | None = None,
    timed_out: bool = False,
    **process: Any,
) -> Replay:
    """Run one turn of `backend` against a recorded (or given) stream; with
    `timed_out` the turn outlives `request.timeout_s`."""
    if backend is Backend.CLAUDE:
        query = FakeQuery(
            messages if messages is not None else claude_messages(fixture or ""),
            hang=timed_out,
        )
        run = Replay(ClaudeAdapter(query_fn=query), query=query)
    else:
        recorded = lines if lines is not None else fixture_lines(backend, fixture or "")
        spawn = FakeSpawn(FakeProcess(recorded, timed_out=timed_out, **process))
        adapter: AgentAdapter = (
            AgyAdapter(
                spawn=spawn, executable="agy", list_models=lambda: [AGY_MODEL_ID]
            )
            if backend is Backend.AGY
            else CodexAdapter(spawn=spawn, executable="codex")
        )
        run = Replay(adapter, spawn=spawn)
    handle = (
        run.adapter.start(request)
        if resume is None
        else run.adapter.resume(resume, request)
    )
    try:
        run.events = list(handle.events())
        run.final = handle.result()
    except AgentError as error:
        run.error = error
    return run


@dataclass(frozen=True)
class Case:
    backend: Backend
    fixture: str
    session_id: str
    tools: tuple[str, ...]
    structured: object | None = None
    schema: bool = False
    resume: str | None = None
    text: str | None = None


CASES = [
    Case(
        Backend.AGY,
        "text_simple",
        "0f79d22b-ebae-4c46-8db6-6327c3c4db52",
        tools=(),
        text="PONG\n",
    ),
    Case(
        Backend.AGY,
        "schema_with_tools",
        "f87452b9-44d7-40c9-ac1e-fbd5fde78293",
        tools=("run_command", "view_file"),
        structured={"answer": "red", "items": [{"source": "a", "target": "b"}]},
        schema=True,
    ),
    Case(
        Backend.AGY,
        "mcp_image",
        "9cbca5fb-d55c-421a-9618-765dcbfc2523",
        tools=("view_file", "secret_picture"),
        text="green\n",
    ),
    # No `finish` step in this turn: agy's `structured_output` is the
    # previous turn's stale value and must not be trusted.
    Case(
        Backend.AGY,
        "resume",
        "8e461142-3acb-43f3-8ecb-26a4491006e5",
        tools=(),
        structured=None,
        schema=True,
        resume="8e461142-3acb-43f3-8ecb-26a4491006e5",
    ),
    Case(
        Backend.CODEX,
        "mcp_schema",
        "01a1233e-ac1a-7111-a4ca-9c25b6431053",
        tools=("session_info", "secret_picture"),
        structured={
            "answer": "SESSION=<none> ARGV=--session codex-arg | 綠色",
            "items": [{"source": "a", "target": "b"}],
        },
        schema=True,
    ),
    Case(
        Backend.CODEX,
        "resume",
        "01a1233c-decf-7c50-8c42-0f0bd1a39e04",
        tools=(),
        structured={
            "answer": "SESSION=<NONE> ARGV=--SESSION CODEX-ARG | GREEN",
            "items": [{"source": "a", "target": "b"}],
        },
        schema=True,
        resume="01a1233c-decf-7c50-8c42-0f0bd1a39e04",
    ),
    Case(
        Backend.CLAUDE,
        "mcp_schema",
        "22bfb9d2-29d6-4b36-aa20-fc7e004877fb",
        tools=("ToolSearch", "session_info", "secret_picture"),
        structured={
            "answer": "SESSION=<none> ARGV=--session claude-arg | green",
            "items": [{"source": "a", "target": "b"}],
        },
        schema=True,
    ),
    Case(
        Backend.CLAUDE,
        "resume",
        "22bfb9d2-29d6-4b36-aa20-fc7e004877fb",
        tools=(),
        structured={
            "answer": "SESSION=<NONE> ARGV=--SESSION CLAUDE-ARG | GREEN",
            "items": [{"source": "a", "target": "b"}],
        },
        schema=True,
        resume="22bfb9d2-29d6-4b36-aa20-fc7e004877fb",
    ),
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.backend}-{c.fixture}")
def test_recorded_turn_normalizes_events_and_final_output(
    case: Case, make_request: Callable[..., TurnRequest]
):
    request = make_request(
        spec=SPECS[case.backend], schema=SCHEMA if case.schema else None
    )
    run = replay(case.backend, request, fixture=case.fixture, resume=case.resume)

    assert run.error is None
    assert run.final is not None
    assert run.final.session_id == case.session_id
    assert run.final.structured == case.structured
    if case.text is not None:
        assert run.final.text == case.text
    calls = [event.name for event in run.events if isinstance(event, ToolCall)]
    results = [event.name for event in run.events if isinstance(event, ToolResult)]
    assert tuple(calls) == case.tools
    assert tuple(results) == case.tools
    assert run.final.usage["input_tokens"] > 0
    assert run.final.usage["output_tokens"] > 0


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.backend}-{c.fixture}")
def test_every_raw_record_reaches_the_raw_sink(
    case: Case, make_request: Callable[..., TurnRequest]
):
    raw: list[str] = []
    request = make_request(spec=SPECS[case.backend], raw=raw.append)
    replay(case.backend, request, fixture=case.fixture, resume=case.resume)

    expected = fixture_lines(case.backend, case.fixture)
    assert [json.loads(line) for line in raw] == [json.loads(line) for line in expected]


def test_mcp_tool_calls_carry_their_arguments(make_request: Callable[..., TurnRequest]):
    request = make_request(spec=SPECS[Backend.CODEX], schema=SCHEMA)
    run = replay(Backend.CODEX, request, fixture="mcp_schema")
    first = next(event for event in run.events if isinstance(event, ToolCall))
    assert first == ToolCall("session_info", {})


def test_intermediate_messages_are_reported(make_request: Callable[..., TurnRequest]):
    request = make_request(spec=SPECS[Backend.CLAUDE])
    run = replay(Backend.CLAUDE, request, fixture="mcp_schema")
    messages = [event.text for event in run.events if isinstance(event, Message)]
    assert messages[0].startswith("I'll help you call those tools")


# --- error classification ------------------------------------------------------


def test_agy_error_result_closes_stdin_and_classifies(
    make_request: Callable[..., TurnRequest],
):
    request = make_request(spec=SPECS[Backend.AGY])
    run = replay(Backend.AGY, request, fixture="error_input")
    assert isinstance(run.error, AgentTransientError)
    assert "stream input" in str(run.error)
    assert run.spawn is not None
    # Closed right after the `result` record (line 2), not at EOF.
    assert run.spawn.processes[0].stdin_closed_after == 2


def test_agy_closes_stdin_as_soon_as_the_result_arrives(
    make_request: Callable[..., TurnRequest],
):
    lines = [*fixture_lines("agy", "text_simple"), '{"event":"warning"}']
    request = make_request(spec=SPECS[Backend.AGY])
    run = replay(Backend.AGY, request, lines=lines)
    assert run.spawn is not None
    assert run.spawn.processes[0].stdin_closed_after == len(lines) - 1


def test_codex_unsupported_model_is_a_config_error(
    make_request: Callable[..., TurnRequest],
):
    request = make_request(spec=SPECS[Backend.CODEX])
    run = replay(Backend.CODEX, request, fixture="error_model", returncode=1)
    assert isinstance(run.error, AgentConfigError)
    assert "not supported" in str(run.error)


def test_codex_recoverable_stream_errors_do_not_fail_a_completed_turn(
    make_request: Callable[..., TurnRequest],
):
    lines = fixture_lines("codex", "resume")
    lines.insert(2, json.dumps({"type": "error", "message": "Reconnecting... 1/5"}))
    request = make_request(spec=SPECS[Backend.CODEX], schema=SCHEMA)
    run = replay(Backend.CODEX, request, lines=lines)
    assert run.error is None


def _quota_stream(backend: Backend) -> dict[str, Any]:
    if backend is Backend.AGY:
        lines = [
            '{"event":"init","conversation_id":"c1"}',
            json.dumps(
                {
                    "event": "result",
                    "result": {
                        "status": "ERROR",
                        "error": "RESOURCE_EXHAUSTED: quota exceeded for the day",
                    },
                }
            ),
        ]
        return {"lines": lines}
    if backend is Backend.CODEX:
        message = '{"type":"error","status":429,"error":{"message":"usage limit"}}'
        lines = [
            '{"type":"thread.started","thread_id":"t1"}',
            json.dumps({"type": "turn.failed", "error": {"message": message}}),
        ]
        return {"lines": lines, "returncode": 1}
    messages = claude_messages("resume")
    result = messages[-1]
    result.is_error = True
    result.api_error_status = 429
    result.result = "You've hit your session limit"
    return {"messages": messages}


@pytest.mark.parametrize("backend", list(Backend))
def test_quota_failures_are_classified(
    backend: Backend, make_request: Callable[..., TurnRequest]
):
    request = make_request(spec=SPECS[backend])
    run = replay(backend, request, **_quota_stream(backend))
    assert isinstance(run.error, AgentQuotaError)


@pytest.mark.parametrize("backend", [Backend.AGY, Backend.CODEX])
def test_cli_exit_without_a_result_is_transient(
    backend: Backend, make_request: Callable[..., TurnRequest]
):
    request = make_request(spec=SPECS[backend])
    run = replay(backend, request, lines=[], returncode=3, stderr="boom")
    assert isinstance(run.error, AgentTransientError)
    assert "boom" in str(run.error)


@pytest.mark.parametrize("backend", list(Backend))
def test_timeout_is_transient(
    backend: Backend, make_request: Callable[..., TurnRequest]
):
    request = make_request(spec=SPECS[backend], timeout_s=0.05)
    run = replay(backend, request, lines=[], messages=[], timed_out=True)
    assert isinstance(run.error, AgentTransientError)
    assert f"{backend} turn timed out after 0.05s" in str(run.error)


# --- audio verification (agy) ----------------------------------------------------


def _recorded_audio_path() -> Path:
    for line in fixture_lines("agy", "audio_view_file"):
        step = json.loads(line).get("step_update") or {}
        params = (step.get("tool_info") or {}).get("parameters") or {}
        if "AbsolutePath" in params:
            return Path(params["AbsolutePath"])
    raise AssertionError("fixture has no view_file step")


def test_agy_audio_opened_with_view_file_counts_as_heard(
    make_request: Callable[..., TurnRequest],
):
    heard = _recorded_audio_path()
    other = heard.with_name("other.ogg")
    request = make_request(spec=SPECS[Backend.AGY], audio=(heard, other))
    run = replay(Backend.AGY, request, fixture="audio_view_file")
    assert run.final is not None
    (defect,) = run.final.defects
    assert defect.audio == (other,)
    assert str(other) in defect.message
    assert "view_file" in defect.message
    assert run.final.text == "Beep\n"


def test_agy_audio_all_heard_has_no_defects(make_request: Callable[..., TurnRequest]):
    request = make_request(spec=SPECS[Backend.AGY], audio=(_recorded_audio_path(),))
    run = replay(Backend.AGY, request, fixture="audio_view_file")
    assert run.final is not None
    assert run.final.defects == ()


# --- the live recordings (scripts/record_agent_fixture.py) ------------------------


@pytest.mark.parametrize("backend", list(Backend))
def test_live_recording_start_turn(
    backend: Backend, make_request: Callable[..., TurnRequest]
):
    request = make_request(spec=SPECS[backend], schema=SCHEMA)
    run = replay(backend, request, fixture="live_start")
    assert run.final is not None
    assert run.final.structured == {"color": "green", "word": "hello"}
    calls = [event for event in run.events if isinstance(event, ToolCall)]
    assert ToolCall("get_frames", {"times": [1]}) in calls


@pytest.mark.parametrize("backend", list(Backend))
def test_live_recording_resume_turn(
    backend: Backend, make_request: Callable[..., TurnRequest]
):
    start = replay(
        backend,
        make_request(spec=SPECS[backend], schema=SCHEMA),
        fixture="live_start",
    )
    assert start.final is not None
    request = make_request(spec=SPECS[backend], schema=SCHEMA, message="fix")
    run = replay(backend, request, fixture="live_resume", resume=start.final.session_id)
    assert run.final is not None
    assert run.final.session_id == start.final.session_id
    assert run.final.structured == {"color": "green", "word": "HELLO"}
