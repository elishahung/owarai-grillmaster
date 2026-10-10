from __future__ import annotations

import sys
from dataclasses import replace
from typing import TYPE_CHECKING

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import TextContent

from grillmaster.agent_tools.__main__ import main
from grillmaster.agent_tools.server import build_server
from grillmaster.core.srt import write_srt_file
from grillmaster.core.tool_session import SESSION_ENV_VAR

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fakes import FakeFfmpeg

    from grillmaster.core.srt import SrtBlock
    from grillmaster.core.tool_session import SrtCheckTool, ToolSession


def _tool_names(session: ToolSession, runner: FakeFfmpeg) -> set[str]:
    async def names() -> set[str]:
        return {tool.name for tool in await build_server(session, runner).list_tools()}

    return anyio.run(names)


def test_server_exposes_only_configured_tools(
    session: ToolSession, fake_ffmpeg: FakeFfmpeg
):
    assert _tool_names(session, fake_ffmpeg) == {"get_frames", "check_srt"}
    no_frames = session.model_copy(update={"frames": None})
    assert _tool_names(no_frames, fake_ffmpeg) == {"check_srt"}
    nothing = session.model_copy(update={"frames": None, "check_srt": None})
    assert _tool_names(nothing, fake_ffmpeg) == set()


def test_validation_failure_surfaces_as_tool_error(
    session: ToolSession, fake_ffmpeg: FakeFfmpeg
):
    server = build_server(session, fake_ffmpeg)

    async def call() -> None:
        await server.call_tool("get_frames", {"times": [1.0]})

    with pytest.raises(ToolError, match="outside the allowed window"):
        anyio.run(call)


def test_stdio_round_trip_check_srt(
    session: ToolSession, tmp_path: Path, reference_blocks: list[SrtBlock]
):
    manifest = tmp_path / "session" / "tools.json"
    session.write(manifest)
    write_srt_file(
        tmp_path / "refined.srt", [replace(b, text="改寫") for b in reference_blocks]
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "grillmaster.agent_tools", "--session", str(manifest)],
    )

    async def round_trip() -> tuple[set[str], list[str]]:
        with anyio.fail_after(60):
            async with (
                stdio_client(params) as (read, write),
                ClientSession(read, write) as client,
            ):
                await client.initialize()
                tools = {tool.name for tool in (await client.list_tools()).tools}
                result = await client.call_tool(
                    "check_srt", {"path": str(tmp_path / "refined.srt")}
                )
        assert not result.isError
        texts = [c.text for c in result.content if isinstance(c, TextContent)]
        return tools, texts

    tools, texts = anyio.run(round_trip)
    assert tools == {"get_frames", "check_srt"}
    assert texts == ["VALID"]


def test_main_without_a_session_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.delenv(SESSION_ENV_VAR, raising=False)
    assert main([]) != 0
    assert "pass --session or set GRILL_TOOL_SESSION" in capsys.readouterr().err


def test_main_reports_a_missing_manifest_from_env(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    missing = tmp_path / "tools.json"
    monkeypatch.setenv(SESSION_ENV_VAR, str(missing))
    assert main([]) != 0
    assert f"invalid tool session {missing}" in capsys.readouterr().err


def test_main_reports_an_invalid_manifest(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
):
    manifest = tmp_path / "tools.json"
    manifest.write_text('{"project_root": "x", "unexpected": 1}', encoding="utf-8")
    assert main(["--session", str(manifest)]) != 0
    assert "invalid tool session" in capsys.readouterr().err


def test_main_reports_an_unreadable_reference(
    session: ToolSession,
    srt_config: SrtCheckTool,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    manifest = tmp_path / "tools.json"
    session.write(manifest)
    srt_config.reference_srt.unlink()
    assert main(["--session", str(manifest)]) != 0
    assert "invalid tool session" in capsys.readouterr().err
