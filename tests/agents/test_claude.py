from __future__ import annotations

import asyncio
import base64
import subprocess
import sys
from typing import TYPE_CHECKING, Any, cast

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    CLINotFoundError,
    RateLimitEvent,
    ResultMessage,
    SystemMessage,
    TextBlock,
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
from grillmaster.core.process import LIVE_PROCESSES, adopt_tree, track

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

    from anyio.abc import Process

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


def _rate_limited(text: str) -> AssistantMessage:
    return AssistantMessage([TextBlock(text)], "claude-opus-5-5", error="rate_limit")


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
    messages = claude_messages("live_resume")
    query = FakeQuery(messages)
    request = make_request(spec=SPEC, message="fix it")
    final = ClaudeAdapter(query_fn=query).resume("sess-9", request).result()
    call = query.calls[0]
    assert call["prompt"] == "fix it"
    assert call["options"].resume == "sess-9"
    assert final.session_id == messages[-1].session_id


def test_each_turn_spawns_the_cli_through_a_registered_transport(
    make_request: Callable[..., TurnRequest],
):
    query = FakeQuery(claude_messages("live_resume"))
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
        seen.append(
            [p.pid for p in LIVE_PROCESSES.live() if isinstance(p.leader, CliProcess)]
        )
        await transport.close()

    before = LIVE_PROCESSES.live()
    asyncio.run(spawn_and_close())  # noqa: TID251 - drives the SDK transport directly
    [pids] = seen
    assert len(pids) == 1
    assert LIVE_PROCESSES.live() == before


class _PopenAsAnyio:
    """The bits of an anyio `Process` that `CliProcess` reads, over a `Popen`."""

    def __init__(self, popen: subprocess.Popen[bytes]) -> None:  # noqa: TID251 - a type only
        self._popen = popen

    @property
    def pid(self) -> int:
        return self._popen.pid

    @property
    def returncode(self) -> int | None:
        return self._popen.poll()

    def kill(self) -> None:
        self._popen.kill()


def test_close_kills_a_cli_the_sdk_left_running():
    # A turn timeout cancels the SDK's `close` before its kill escalation;
    # here the SDK side has nothing to close, and the CLI still runs.
    # Spawned like the SDK spawns its CLI, then adopted like `connect` does.
    cli = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])  # noqa: TID251
    try:
        tree = track(adopt_tree(CliProcess(cast("Process", _PopenAsAnyio(cli)))))
        transport = RegisteredTransport(prompt="hi", options=ClaudeAgentOptions())
        transport._tree = tree  # what `connect` would have set

        asyncio.run(transport.close())  # noqa: TID251 - drives the SDK transport directly

        assert cli.wait(timeout=10) != 0
        assert tree not in LIVE_PROCESSES.live()
    finally:
        cli.kill()
        cli.wait()


def test_images_travel_as_base64_blocks(
    make_request: Callable[..., TurnRequest], tmp_path: Path
):
    image = tmp_path / "frame.png"
    image.write_bytes(b"\x89PNG fake")
    query = FakeQuery(claude_messages("live_resume"))
    ClaudeAdapter(query_fn=query).start(
        make_request(spec=SPEC, images=(image,), message="look")
    ).result()

    async def first(stream: AsyncIterator[dict[str, Any]]) -> dict[str, Any]:
        return await anext(stream)

    # Reads the prompt stream the adapter built; no pipeline code involved.
    message = asyncio.run(first(query.calls[0]["prompt"]))  # noqa: TID251 - drains an async prompt stream
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
    messages = claude_messages("live_resume")
    query = FakeQuery([tick, *messages, tick])
    ClaudeAdapter(query_fn=query).start(
        make_request(spec=SPEC, raw=raw.append)
    ).result()
    assert len(raw) == len(messages)
    assert all("thinking_tokens" not in line for line in raw)


def test_structured_output_tool_is_not_reported_as_a_tool_call(
    make_request: Callable[..., TurnRequest],
):
    handle = ClaudeAdapter(query_fn=FakeQuery(claude_messages("live_resume"))).start(
        make_request(spec=SPEC)
    )
    assert all(
        getattr(event, "name", "") != "StructuredOutput" for event in handle.events()
    )


@pytest.mark.parametrize(
    ("messages", "failure", "error"),
    [
        (
            [_result(is_error=True, api_error_status=429, result="slow down")],
            None,
            AgentTransientError,
        ),
        (
            [
                _result(
                    is_error=True,
                    api_error_status=429,
                    result="Claude AI usage limit reached",
                )
            ],
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
        (
            [_rate_limited("Claude AI usage limit reached")],
            RuntimeError("Command failed with exit code 1"),
            AgentQuotaError,
        ),
        (
            [_rate_limited("Too many requests, retry later")],
            RuntimeError("Command failed with exit code 1"),
            AgentTransientError,
        ),
        ([], CLINotFoundError("missing"), AgentConfigError),
        ([], RuntimeError("crashed"), AgentTransientError),
        ([], None, AgentTransientError),
    ],
    ids=[
        "429",
        "429-usage-limit",
        "401",
        "400",
        "529",
        "rate-limit-event",
        "rate-limit-usage",
        "rate-limit-bare",
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
