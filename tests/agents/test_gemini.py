from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from tests.agents.fakes import FakeProcess, FakeSpawn

from grillmaster.agents.adapters._google import API_KEY_ENV_VARS
from grillmaster.agents.adapters.base import McpServer
from grillmaster.agents.adapters.gemini import (
    LITERAL_AT,
    MAX_MEDIA_BYTES,
    GeminiAdapter,
    GeminiTurnParser,
)
from grillmaster.agents.errors import AgentConfigError
from grillmaster.agents.events import Message, ToolCall, ToolResult
from grillmaster.core.model_spec import Backend, Effort, ModelSpec

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from grillmaster.agents.adapters.base import TurnRequest

FLASH_LOW = ModelSpec(Backend.GEMINI, "gemini-3.8-flash", Effort.LOW)
# The smallest successful turn.
TURN = [
    '{"type":"init","session_id":"s1","model":"gemini-3.8-flash"}',
    '{"type":"message","role":"assistant","content":"ok","delta":true}',
    json.dumps(
        {
            "type": "result",
            "status": "success",
            "stats": {"total_tokens": 3, "input_tokens": 2, "output_tokens": 1},
        }
    ),
]


def _adapter(spawn: FakeSpawn) -> GeminiAdapter:
    return GeminiAdapter(spawn=spawn, executable="gemini")


def _run(adapter: GeminiAdapter, request: TurnRequest, *, resume: str | None = None):
    handle = (
        adapter.start(request) if resume is None else adapter.resume(resume, request)
    )
    return handle.result()


def _spawn(count: int = 1) -> FakeSpawn:
    return FakeSpawn(*(FakeProcess(TURN) for _ in range(count)))


def _settings(request: TurnRequest) -> dict[str, Any]:
    path = request.workdir / ".gemini" / "settings.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_start_attaches_media_as_at_tokens_and_includes_their_folders(
    make_request: Callable[..., TurnRequest], tmp_path: Path
):
    image = tmp_path / "frames" / "a.jpg"
    audio = tmp_path / "chunk" / "audio.ogg"
    extra = tmp_path / "project"
    spawn = _spawn()
    request = make_request(
        spec=FLASH_LOW, images=(image,), audio=(audio,), add_dirs=(extra,)
    )
    _run(_adapter(spawn), request)

    spec = spawn.specs[0]
    argv = list(spec.argv)
    assert argv[0] == "gemini"
    assert argv[argv.index("--model") + 1] == "gemini-3.8-flash"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--approval-mode") + 1] == "yolo"
    assert argv[argv.index("--allowed-mcp-server-names") + 1] == "grill"
    assert "--skip-trust" in argv
    included = [
        argv[i + 1] for i, arg in enumerate(argv) if arg == "--include-directories"
    ]
    assert included == [str(extra), str(image.parent), str(audio.parent)]
    assert "--resume" not in argv
    assert argv[-1] == "--prompt="
    assert spec.cwd == request.workdir
    assert spec.stdin == f'hello\n\n@"{image}"\n@"{audio}"'


def test_the_messages_own_at_signs_are_not_at_commands(
    make_request: Callable[..., TurnRequest], tmp_path: Path
):
    image = tmp_path / "my frames" / "a.jpg"
    spawn = _spawn()
    message = "@ねこまる 草\nmail foo@bar.com"
    _run(_adapter(spawn), make_request(message=message, images=(image,)))
    literal = message.replace("@", LITERAL_AT)
    assert spawn.specs[0].stdin == f'{literal}\n\n@"{image}"'
    assert "@" not in literal


def test_resume_repeats_the_flags_and_names_the_session(
    make_request: Callable[..., TurnRequest],
):
    spawn = _spawn(2)
    adapter = _adapter(spawn)
    _run(adapter, make_request(spec=FLASH_LOW))
    _run(adapter, make_request(spec=FLASH_LOW, message="fix"), resume="s-1")

    start, resumed = (list(spec.argv) for spec in spawn.specs)
    index = resumed.index("--resume")
    assert resumed[index + 1] == "s-1"
    del resumed[index : index + 2]
    assert resumed == start
    assert spawn.specs[1].stdin == "fix"


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
    spec = ModelSpec(Backend.GEMINI, "gemini-3.8-flash", effort)
    request = make_request(spec=spec)
    _run(_adapter(_spawn()), request)

    (override,) = _settings(request)["modelConfigs"]["overrides"]
    assert override["match"] == {"model": "gemini-3.8-flash"}
    thinking = override["modelConfig"]["generateContentConfig"]["thinkingConfig"]
    assert thinking == {"thinkingLevel": level}


def test_gemini_2_5_gets_a_thinking_budget(make_request: Callable[..., TurnRequest]):
    request = make_request(spec=ModelSpec(Backend.GEMINI, "gemini-2.5-pro", Effort.LOW))
    _run(_adapter(_spawn()), request)
    (override,) = _settings(request)["modelConfigs"]["overrides"]
    thinking = override["modelConfig"]["generateContentConfig"]["thinkingConfig"]
    assert thinking == {"thinkingBudget": 1024}


def test_mcp_server_goes_into_the_workspace_settings_and_leaves_with_it(
    make_request: Callable[..., TurnRequest],
):
    adapter = _adapter(_spawn(2))
    mcp = McpServer("python", ("-m", "grillmaster.agent_tools", "--session", "t.json"))
    request = make_request(spec=FLASH_LOW, mcp=mcp)
    _run(adapter, request)
    assert _settings(request)["mcpServers"] == {
        "grill": {
            "command": "python",
            "args": ["-m", "grillmaster.agent_tools", "--session", "t.json"],
        }
    }

    _run(adapter, make_request(spec=FLASH_LOW))
    assert "mcpServers" not in _settings(request)


def test_paid_api_keys_are_scrubbed(
    make_request: Callable[..., TurnRequest], monkeypatch: pytest.MonkeyPatch
):
    for key in API_KEY_ENV_VARS:
        monkeypatch.setenv(key, "paid")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    spawn = _spawn()
    _run(_adapter(spawn), make_request(spec=FLASH_LOW))

    env = spawn.specs[0].env
    assert env is not None
    assert not set(API_KEY_ENV_VARS) & set(env)
    assert env["GOOGLE_CLOUD_PROJECT"] == "proj"


def test_preflight_refuses_media_the_cli_cannot_attach(tmp_path: Path):
    adapter = _adapter(FakeSpawn())
    large = tmp_path / "large.ogg"
    with large.open("wb") as handle:
        handle.truncate(MAX_MEDIA_BYTES + 1)
    with pytest.raises(AgentConfigError, match="20 MB"):
        adapter.preflight(FLASH_LOW, (), (large,))

    fine = tmp_path / "with space.ogg"  # quoted in its `@"path"` token
    fine.write_bytes(b"x")
    adapter.preflight(FLASH_LOW, (fine,), (fine,))


def _feed(parser: GeminiTurnParser, records: list[dict[str, Any]]) -> list[Any]:
    return [event for record in records for event in parser.feed(record)]


def _result(**stats: int) -> dict[str, Any]:
    return {"type": "result", "status": "success", "stats": stats}


def test_messages_split_at_tool_calls_and_the_last_one_is_the_answer():
    parser = GeminiTurnParser()
    events = _feed(
        parser,
        [
            {"type": "init", "session_id": "s1"},
            {"type": "message", "role": "user", "content": "go"},
            {
                "type": "message",
                "role": "assistant",
                "content": "Checking",
                "delta": True,
            },
            {
                "type": "message",
                "role": "assistant",
                "content": " first.",
                "delta": True,
            },
            {
                "type": "tool_use",
                "tool_name": "mcp_grill_check_srt",
                "tool_id": "t1",
                "parameters": {"path": "a.srt"},
            },
            {"type": "tool_result", "tool_id": "t1", "status": "error"},
            {"type": "message", "role": "assistant", "content": '{"a":', "delta": True},
            {"type": "message", "role": "assistant", "content": " 1}", "delta": True},
            _result(total_tokens=30, input_tokens=20, output_tokens=4, cached=5),
        ],
    )
    assert events == [
        Message("Checking first."),
        ToolCall("check_srt", {"path": "a.srt"}),
        ToolResult("check_srt", ok=False),
        Message('{"a": 1}'),
    ]
    final = parser.finish(0, "")
    assert final.session_id == "s1"
    assert final.text == '{"a": 1}'
    assert final.structured is None  # the runner parses `text`
    assert final.usage == {
        "input_tokens": 20,
        "output_tokens": 4,
        "cached_input_tokens": 5,
        "reasoning_tokens": 6,
    }
