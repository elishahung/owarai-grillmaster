from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any

import pytest
from tests.agents.fakes import (
    FakeProcess,
    FakeSpawn,
    acp_handshake,
    acp_prompt_result,
    acp_response,
    acp_update,
)

from grillmaster.agents.adapters._google import API_KEY_ENV_VARS
from grillmaster.agents.adapters.base import McpServer
from grillmaster.agents.adapters.gemini import MAX_MEDIA_BYTES, GeminiAdapter
from grillmaster.agents.errors import (
    AgentAuthError,
    AgentConfigError,
    AgentTransientError,
)
from grillmaster.agents.events import Message, Thought, ToolCall, ToolResult
from grillmaster.core.model_spec import Backend, Effort, ModelSpec

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from grillmaster.agents.adapters.base import FinalOutput, TurnRequest
    from grillmaster.agents.adapters.gemini import GeminiSession

FLASH_LOW = ModelSpec(Backend.GEMINI, "gemini-3.8-flash", Effort.LOW)


def _answer(text: str = "ok") -> dict[str, Any]:
    return {
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": text},
    }


# The smallest successful first turn: the handshake, then prompt id 3.
# The answer to the first prompt (id 3), and the smallest successful first turn.
ANSWER = [acp_update(_answer()), acp_prompt_result(3)]
TURN = [*acp_handshake(), *ANSWER]


def _session(*lines: str) -> tuple[GeminiSession, FakeSpawn]:
    spawn = FakeSpawn(FakeProcess(list(lines) or TURN))
    return GeminiAdapter(spawn=spawn, executable="gemini").session(), spawn


def _sent(spawn: FakeSpawn) -> list[dict[str, Any]]:
    return [json.loads(text) for text in spawn.processes[0].sent]


def _settings(request: TurnRequest) -> dict[str, Any]:
    path = request.workdir / ".gemini" / "settings.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _first_turn(request: TurnRequest, *lines: str) -> FinalOutput:
    session, _ = _session(*lines)
    return session.start(request).result()


def test_start_runs_acp_in_the_workdir_and_attaches_media_inline(
    make_request: Callable[..., TurnRequest], tmp_path: Path
):
    image = tmp_path / "frames" / "a.jpg"
    audio = tmp_path / "chunk" / "audio.ogg"
    for path, data in ((image, b"jpeg"), (audio, b"ogg")):
        path.parent.mkdir(parents=True)
        path.write_bytes(data)
    extra = tmp_path / "project"
    session, spawn = _session()
    request = make_request(
        spec=FLASH_LOW, images=(image,), audio=(audio,), add_dirs=(extra,)
    )
    final = session.start(request).result()

    spec = spawn.specs[0]
    argv = list(spec.argv)
    assert argv[:2] == ["gemini", "--acp"]
    assert argv[argv.index("--model") + 1] == "gemini-3.8-flash"
    assert argv[argv.index("--approval-mode") + 1] == "yolo"
    assert argv[argv.index("--allowed-mcp-server-names") + 1] == "grill"
    assert "--skip-trust" in argv
    assert argv[argv.index("--include-directories") + 1] == str(extra)
    assert spec.cwd == request.workdir
    assert spec.keep_stdin_open
    initialize, new, prompt = _sent(spawn)
    assert (initialize["id"], initialize["method"]) == (1, "initialize")
    assert new["method"] == "session/new"
    assert new["params"] == {"cwd": str(request.workdir), "mcpServers": []}
    assert prompt["method"] == "session/prompt"
    assert prompt["params"]["sessionId"] == "s1"
    assert prompt["params"]["prompt"] == [
        {"type": "text", "text": "hello"},
        {"type": "image", "mimeType": "image/jpeg", "data": _b64(b"jpeg")},
        {"type": "audio", "mimeType": "audio/ogg", "data": _b64(b"ogg")},
    ]
    assert final.session_id == "s1"
    assert final.text == "ok"
    assert final.usage == {"input_tokens": 2, "output_tokens": 1}


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_the_message_goes_verbatim(make_request: Callable[..., TurnRequest]):
    # ACP text is not parsed for `@` commands.
    message = "@ねこまる 草\nmail foo@bar.com"
    session, spawn = _session()
    session.start(make_request(spec=FLASH_LOW, message=message)).result()
    assert _sent(spawn)[2]["params"]["prompt"] == [{"type": "text", "text": message}]


def test_resume_prompts_the_same_process(make_request: Callable[..., TurnRequest]):
    lines = [*TURN, acp_update(_answer("fixed")), acp_prompt_result(4, tokens=(9, 3))]
    session, spawn = _session(*lines)
    first = session.start(make_request(spec=FLASH_LOW)).result()
    resumed = session.resume(
        first.session_id, make_request(spec=FLASH_LOW, message="fix", timeout_s=7.0)
    ).result()

    assert len(spawn.specs) == 1
    *_, prompt = _sent(spawn)
    assert (prompt["id"], prompt["method"]) == (4, "session/prompt")
    assert prompt["params"] == {
        "sessionId": "s1",
        "prompt": [{"type": "text", "text": "fix"}],
    }
    assert (resumed.session_id, resumed.text) == ("s1", "fixed")
    assert resumed.usage == {"input_tokens": 9, "output_tokens": 3}
    # Each turn is bounded on its own; nothing runs out between turns.
    assert spawn.processes[0].watchdog == [60.0, None, 7.0, None]


def test_a_resume_names_this_session(make_request: Callable[..., TurnRequest]):
    session, _ = _session()
    with pytest.raises(RuntimeError):
        session.resume("s1", make_request(spec=FLASH_LOW))
    session.start(make_request(spec=FLASH_LOW)).result()
    with pytest.raises(RuntimeError):
        session.resume("other", make_request(spec=FLASH_LOW))


def test_close_ends_the_process_once(make_request: Callable[..., TurnRequest]):
    session, spawn = _session()
    session.start(make_request(spec=FLASH_LOW)).result()
    session.close()
    session.close()
    process = spawn.processes[0]
    assert process.stopped_early  # stdout closed: the real tree is killed
    assert process.waits == 1


def test_stopping_a_turn_early_ends_the_session(
    make_request: Callable[..., TurnRequest],
):
    tool = {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "x()"}
    lines = [
        *acp_handshake(),
        acp_update(_answer("a")),
        acp_update(tool),
        *ANSWER,
    ]
    session, spawn = _session(*lines)
    events = session.start(make_request(spec=FLASH_LOW)).events()
    assert next(events) == Message("a")
    events.close()
    assert spawn.processes[0].stopped_early


@pytest.mark.parametrize(
    ("effort", "level"),
    [
        (Effort.LOW, "LOW"),
        (Effort.MEDIUM, "MEDIUM"),
        (Effort.HIGH, "HIGH"),
        (Effort.ULTRA, "HIGH"),
    ],
)
def test_effort_becomes_the_thinking_level_of_the_model(
    make_request: Callable[..., TurnRequest], effort: Effort, level: str
):
    request = make_request(spec=ModelSpec(Backend.GEMINI, "gemini-3.8-flash", effort))
    _first_turn(request)

    settings = _settings(request)
    assert settings["model"] == {"compressionThreshold": 1000.0}
    # The workdir listing would show the model its attached audio as a file.
    assert settings["context"] == {"includeDirectoryTree": False}
    (override,) = settings["modelConfigs"]["overrides"]
    assert override["match"] == {"model": "gemini-3.8-flash"}
    thinking = override["modelConfig"]["generateContentConfig"]["thinkingConfig"]
    assert thinking == {"thinkingLevel": level}


def test_gemini_2_5_gets_a_thinking_budget(make_request: Callable[..., TurnRequest]):
    request = make_request(spec=ModelSpec(Backend.GEMINI, "gemini-2.5-pro", Effort.LOW))
    _first_turn(request)
    (override,) = _settings(request)["modelConfigs"]["overrides"]
    thinking = override["modelConfig"]["generateContentConfig"]["thinkingConfig"]
    assert thinking == {"thinkingBudget": 1024}


def test_mcp_server_goes_into_the_workspace_settings_and_leaves_with_it(
    make_request: Callable[..., TurnRequest],
):
    mcp = McpServer("python", ("-m", "grillmaster.agent_tools", "--session", "t.json"))
    request = make_request(spec=FLASH_LOW, mcp=mcp)
    _first_turn(request)
    assert _settings(request)["mcpServers"] == {
        "grill": {
            "command": "python",
            "args": ["-m", "grillmaster.agent_tools", "--session", "t.json"],
        }
    }

    _first_turn(make_request(spec=FLASH_LOW))
    assert "mcpServers" not in _settings(request)


def test_paid_api_keys_are_scrubbed(
    make_request: Callable[..., TurnRequest], monkeypatch: pytest.MonkeyPatch
):
    for key in API_KEY_ENV_VARS:
        monkeypatch.setenv(key, "paid")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    session, spawn = _session()
    session.start(make_request(spec=FLASH_LOW)).result()

    env = spawn.specs[0].env
    assert env is not None
    assert not set(API_KEY_ENV_VARS) & set(env)
    assert env["GOOGLE_CLOUD_PROJECT"] == "proj"


def test_preflight_refuses_media_the_model_cannot_take(tmp_path: Path):
    adapter = GeminiAdapter(spawn=FakeSpawn(), executable="gemini")
    half = tmp_path / "half.ogg"
    with half.open("wb") as handle:
        handle.truncate(MAX_MEDIA_BYTES // 2 + 1)
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"x" * (MAX_MEDIA_BYTES // 2))
    adapter.preflight(FLASH_LOW, (), (half,))
    # The limit is on the request: every attachment goes inline in one prompt.
    with pytest.raises(AgentConfigError, match="20 MB per request"):
        adapter.preflight(FLASH_LOW, (frame,), (half,))
    odd = tmp_path / "clip.aiff"
    odd.write_bytes(b"x")
    with pytest.raises(AgentConfigError, match=r"\.ogg"):
        adapter.preflight(FLASH_LOW, (), (odd,))

    image, audio = tmp_path / "a.PNG", tmp_path / "with space.ogg"
    image.write_bytes(b"x")
    audio.write_bytes(b"x")
    adapter.preflight(FLASH_LOW, (image,), (audio,))


def test_updates_become_messages_split_at_tool_calls(
    make_request: Callable[..., TurnRequest],
):
    tool = {
        "sessionUpdate": "tool_call",
        "toolCallId": "t1",
        "status": "in_progress",
        "title": "check_srt(path: a.srt)",
    }
    done = {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "failed"}

    def thought(text: str) -> dict[str, Any]:
        content = {"type": "text", "text": text}
        return {"sessionUpdate": "agent_thought_chunk", "content": content}

    lines = [
        *acp_handshake(),
        acp_update(thought("**Plan**\nread it")),
        # A continuation chunk: gemini-cli prints its empty subject as `****`.
        acp_update(thought("****\nthen check")),
        acp_update(thought("****\n")),
        acp_update(_answer("Checking")),
        acp_update(_answer(" first.")),
        acp_update(tool),
        acp_update(done),
        acp_update(_answer('{"a":')),
        acp_update(_answer(" 1}")),
        acp_update(_answer("not ours"), session_id="other"),
        acp_prompt_result(3),
    ]
    session, _ = _session(*lines)
    handle = session.start(make_request(spec=FLASH_LOW))
    assert list(handle.events()) == [
        Thought("**Plan**\nread it"),
        Thought("then check"),
        Message("Checking first."),
        ToolCall("check_srt", {"args": "path: a.srt"}),
        ToolResult("check_srt", ok=False),
        Message('{"a": 1}'),
    ]
    final = handle.result()
    assert final.text == '{"a": 1}'
    assert final.structured is None  # the runner parses `text`


def test_permission_requests_are_approved(make_request: Callable[..., TurnRequest]):
    options = [
        {"optionId": "cancel", "kind": "reject_once"},
        {"optionId": "proceed_once", "kind": "allow_once"},
    ]
    ask = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "session/request_permission",
        "params": {"sessionId": "s1", "options": options},
    }
    lines = [*acp_handshake(), json.dumps(ask), *ANSWER]
    session, spawn = _session(*lines)
    session.start(make_request(spec=FLASH_LOW)).result()
    answer = _sent(spawn)[3]
    assert answer == {
        "jsonrpc": "2.0",
        "id": 0,
        "result": {"outcome": {"outcome": "selected", "optionId": "proceed_once"}},
    }


def test_an_overflow_refusal_is_a_config_error(
    make_request: Callable[..., TurnRequest],
):
    request = make_request(spec=FLASH_LOW)
    with pytest.raises(AgentConfigError, match="context window"):
        _first_turn(request, *acp_handshake(), acp_prompt_result(3, "max_tokens"))


@pytest.mark.parametrize(
    ("error", "expected", "fragment"),
    [
        (
            {"code": 404, "message": "Requested entity was not found."},
            AgentConfigError,
            "no such model",
        ),
        (
            {"code": 400, "message": "Requests ending with a model turn"},
            AgentConfigError,
            "model turn",
        ),
        ({"code": 401, "message": "Unauthorized"}, AgentAuthError, "gemini"),
        ({"code": 500, "message": "socket hang up"}, AgentTransientError, "hang"),
    ],
)
def test_prompt_errors_are_classified(
    make_request: Callable[..., TurnRequest],
    error: dict[str, Any],
    expected: type[Exception],
    fragment: str,
):
    lines = [*acp_handshake(), acp_response(3, error=error)]
    with pytest.raises(expected, match=fragment):
        _first_turn(make_request(spec=FLASH_LOW), *lines)


def test_a_missing_cloud_project_is_a_config_error(
    make_request: Callable[..., TurnRequest],
):
    message = "This account requires setting the GOOGLE_CLOUD_PROJECT env var."
    lines = [
        acp_handshake()[0],
        acp_response(2, error={"code": -32000, "message": message}),
    ]
    with pytest.raises(AgentConfigError, match="needs GOOGLE_CLOUD_PROJECT"):
        _first_turn(make_request(spec=FLASH_LOW), *lines)


def test_startup_failures_are_classified(make_request: Callable[..., TurnRequest]):
    request = make_request(spec=FLASH_LOW)
    no_session = [acp_handshake()[0], acp_response(2, {"sessionId": ""})]
    with pytest.raises(AgentTransientError, match="opened no session"):
        _session(*no_session)[0].start(request)

    session, spawn = _session(acp_handshake()[0])
    with pytest.raises(AgentTransientError, match="without a result"):
        session.start(request)
    assert spawn.processes[0].waits == 1

    spawn = FakeSpawn(FakeProcess([], timed_out=True))
    session = GeminiAdapter(spawn=spawn, executable="gemini").session()
    with pytest.raises(AgentTransientError, match="timed out"):
        session.start(request)


def test_an_answered_turn_is_not_a_timeout(make_request: Callable[..., TurnRequest]):
    # The watchdog fired after the answer arrived.
    spawn = FakeSpawn(FakeProcess(TURN, timed_out=True))
    session = GeminiAdapter(spawn=spawn, executable="gemini").session()
    assert session.start(make_request(spec=FLASH_LOW)).result().text == "ok"


def test_an_unreadable_attachment_fails_the_turn(
    make_request: Callable[..., TurnRequest], tmp_path: Path
):
    gone = tmp_path / "gone.ogg"
    session, spawn = _session()
    turn = session.start(make_request(spec=FLASH_LOW, audio=(gone,)))
    with pytest.raises(AgentTransientError, match="attachment unreadable"):
        turn.result()
    assert len(spawn.processes[0].sent) == 2  # no prompt was sent
