from __future__ import annotations

import asyncio
import base64
import sys
from typing import TYPE_CHECKING, Any

import pytest
from claude_agent_sdk import (
    ClaudeAgentOptions,
    CLINotFoundError,
    RateLimitEvent,
    ResultMessage,
    SystemMessage,
)
from claude_agent_sdk.types import RateLimitInfo
from tests.agents.fakes import FakeQuery, claude_messages

from grillmaster.agents.adapters.base import McpServer
from grillmaster.agents.adapters.claude import (
    ClaudeAdapter,
    CliProcess,
    RegisteredTransport,
    build_options,
    encode_message,
    tool_name,
)
from grillmaster.agents.errors import (
    AgentAuthError,
    AgentConfigError,
    AgentQuotaError,
    AgentTransientError,
)
from grillmaster.core.model_spec import Backend, Effort, ModelSpec
from grillmaster.core.process import LIVE_PROCESSES

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

    from grillmaster.agents.adapters.base import TurnRequest

SPEC = ModelSpec(Backend.CLAUDE, "claude-opus-5-5", Effort.EXTRA)
MCP = McpServer("python", ("-m", "grillmaster.agent_tools", "--session", "t.json"))


def _result(**overrides: Any) -> ResultMessage:
    fields: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 1,
        "duration_api_ms": 1,
        "is_error": False,
        "num_turns": 1,
        "session_id": "s1",
        "result": "ok",
    }
    fields.update(overrides)
    return ResultMessage(**fields)


def test_options_isolate_the_session_and_carry_every_setting(
    make_request: Callable[..., TurnRequest], tmp_path: Path
):
    request = make_request(
        spec=SPEC, schema={"type": "object"}, mcp=MCP, add_dirs=(tmp_path,)
    )
    options = build_options(request, resume="prev")
    assert options.cwd == request.workdir
    assert options.model == "claude-opus-5-5"
    assert options.effort == "xhigh"
    assert options.permission_mode == "bypassPermissions"
    assert options.setting_sources == []
    assert options.strict_mcp_config
    assert options.mcp_servers == {
        "grill": {
            "type": "stdio",
            "command": "python",
            "args": ["-m", "grillmaster.agent_tools", "--session", "t.json"],
        }
    }
    assert options.output_format == {
        "type": "json_schema",
        "schema": {"type": "object"},
    }
    assert options.add_dirs == [tmp_path]
    assert options.resume == "prev"


def test_options_without_tools_or_schema(make_request: Callable[..., TurnRequest]):
    options = build_options(make_request(spec=SPEC), resume=None)
    assert options.mcp_servers == {}
    assert options.output_format is None
    assert options.resume is None


def test_ultra_effort_clamps_to_max(make_request: Callable[..., TurnRequest]):
    spec = ModelSpec(Backend.CLAUDE, "m", Effort.ULTRA)
    assert build_options(make_request(spec=spec), resume=None).effort == "max"


def test_resume_passes_the_session_and_only_the_message(
    make_request: Callable[..., TurnRequest],
):
    query = FakeQuery(claude_messages("resume"))
    request = make_request(spec=SPEC, message="fix it")
    final = ClaudeAdapter(query_fn=query).resume("sess-9", request).result()
    call = query.calls[0]
    assert call["prompt"] == "fix it"
    assert call["options"].resume == "sess-9"
    assert final.session_id == "22bfb9d2-29d6-4b36-aa20-fc7e004877fb"


def test_each_turn_spawns_the_cli_through_a_registered_transport(
    make_request: Callable[..., TurnRequest],
):
    query = FakeQuery(claude_messages("resume"))
    ClaudeAdapter(query_fn=query).start(make_request(spec=SPEC)).result()
    assert isinstance(query.calls[0]["transport"], RegisteredTransport)


def test_registered_transport_holds_its_child_in_live_processes(
    monkeypatch: pytest.MonkeyPatch,
):
    # A real spawn through the SDK's own `connect`: Python stands in for the
    # CLI and exits at once on the CLI flags, which is enough to see the pid.
    monkeypatch.setenv("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "1")
    options = ClaudeAgentOptions(cli_path=sys.executable, stderr=lambda _line: None)
    transport = RegisteredTransport(prompt="hi", options=options)
    seen: list[list[int]] = []

    async def spawn_and_close() -> None:
        await transport.connect()
        seen.append([p.pid for p in LIVE_PROCESSES.live() if isinstance(p, CliProcess)])
        await transport.close()

    before = LIVE_PROCESSES.live()
    asyncio.run(spawn_and_close())  # noqa: TID251
    [pids] = seen
    assert len(pids) == 1
    assert LIVE_PROCESSES.live() == before


def test_images_travel_as_base64_blocks(
    make_request: Callable[..., TurnRequest], tmp_path: Path
):
    image = tmp_path / "frame.png"
    image.write_bytes(b"\x89PNG fake")
    query = FakeQuery(claude_messages("resume"))
    ClaudeAdapter(query_fn=query).start(
        make_request(spec=SPEC, images=(image,), message="look")
    ).result()

    async def first(stream: AsyncIterator[dict[str, Any]]) -> dict[str, Any]:
        return await anext(stream)

    # Reads the prompt stream the adapter built; no pipeline code involved.
    message = asyncio.run(first(query.calls[0]["prompt"]))  # noqa: TID251
    text, block = message["message"]["content"]
    assert text == {"type": "text", "text": "look"}
    assert block["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": base64.standard_b64encode(b"\x89PNG fake").decode(),
    }


def test_unsupported_image_type_is_a_config_error(
    make_request: Callable[..., TurnRequest], tmp_path: Path
):
    image = tmp_path / "frame.bmp"
    image.write_bytes(b"BM")
    adapter = ClaudeAdapter(query_fn=FakeQuery([]))
    adapter.preflight(SPEC, (tmp_path / "ok.PNG",), ())
    with pytest.raises(AgentConfigError, match=r"frame\.bmp"):
        adapter.preflight(SPEC, (image,), ())


def test_thinking_progress_ticks_are_dropped(make_request: Callable[..., TurnRequest]):
    tick = SystemMessage(subtype="thinking_tokens", data={"tokens": 12})
    raw: list[str] = []
    messages = claude_messages("resume")
    query = FakeQuery([tick, *messages, tick])
    ClaudeAdapter(query_fn=query).start(
        make_request(spec=SPEC, raw=raw.append)
    ).result()
    assert len(raw) == len(messages)
    assert all("thinking_tokens" not in line for line in raw)


def test_structured_output_tool_is_not_reported_as_a_tool_call(
    make_request: Callable[..., TurnRequest],
):
    handle = ClaudeAdapter(query_fn=FakeQuery(claude_messages("resume"))).start(
        make_request(spec=SPEC)
    )
    assert all(
        getattr(event, "name", "") != "StructuredOutput" for event in handle.events()
    )


@pytest.mark.parametrize(
    ("messages", "failure", "error"),
    [
        (
            [_result(is_error=True, api_error_status=429, result="limit")],
            None,
            AgentQuotaError,
        ),
        (
            [_result(is_error=True, api_error_status=401, result="expired")],
            None,
            AgentAuthError,
        ),
        (
            [_result(is_error=True, api_error_status=400, result="bad")],
            None,
            AgentConfigError,
        ),
        (
            [_result(is_error=True, api_error_status=529, result="busy")],
            None,
            AgentTransientError,
        ),
        (
            [
                RateLimitEvent(
                    RateLimitInfo(status="rejected", rate_limit_type="five_hour"),
                    "u",
                    "s",
                )
            ],
            RuntimeError("Command failed with exit code 1"),
            AgentQuotaError,
        ),
        ([], CLINotFoundError("missing"), AgentConfigError),
        ([], RuntimeError("crashed"), AgentTransientError),
        ([], None, AgentTransientError),
    ],
    ids=[
        "429",
        "401",
        "400",
        "529",
        "rate-limit-event",
        "no-cli",
        "crash",
        "no-result",
    ],
)
def test_failures_are_classified(
    messages: list[Any],
    failure: BaseException | None,
    error: type[Exception],
    make_request: Callable[..., TurnRequest],
):
    adapter = ClaudeAdapter(query_fn=FakeQuery(messages, failure))
    with pytest.raises(error):
        adapter.start(make_request(spec=SPEC)).result()


def test_auth_failure_names_the_login_command(
    make_request: Callable[..., TurnRequest],
):
    query = FakeQuery([_result(is_error=True, api_error_status=401, result="expired")])
    with pytest.raises(AgentAuthError, match="claude auth login"):
        ClaudeAdapter(query_fn=query).start(make_request(spec=SPEC)).result()


def test_mcp_tool_names_drop_the_server_prefix():
    assert tool_name("mcp__grill__get_frames") == "get_frames"
    assert tool_name("Read") == "Read"


def test_encode_message_tags_dataclasses():
    encoded = encode_message(_result())
    assert encoded["_type"] == "ResultMessage"
    assert encoded["session_id"] == "s1"
