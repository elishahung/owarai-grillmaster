"""Builds the FastMCP server for one tool session.

Only the tools in `session.allowed` are registered; a session with no tool
configs yields a server that lists nothing. Tool descriptions carry the
session's limits so the agent sees them in the tool listing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP

from grillmaster.agent_tools import srt_check
from grillmaster.agent_tools.frames import FrameGrabber
from grillmaster.core.tool_session import ToolName
from grillmaster.media.ffmpeg import SubprocessFfmpegRunner

if TYPE_CHECKING:
    from collections.abc import Callable

    from grillmaster.core.tool_session import ToolSession
    from grillmaster.media.ffmpeg import FfmpegRunner

SERVER_NAME = "grill"


def build_server(session: ToolSession, runner: FfmpegRunner | None = None) -> FastMCP:
    server = FastMCP(SERVER_NAME, log_level="WARNING")
    for name in sorted(session.allowed):
        handler, description = _tool(session, name, runner)
        server.add_tool(
            handler, name=name, description=description, structured_output=False
        )
    return server


def _tool(
    session: ToolSession, name: ToolName, runner: FfmpegRunner | None
) -> tuple[Callable[..., Any], str]:
    """The handler and description of one allowed tool."""
    match name:
        case ToolName.GET_FRAMES if session.frames is not None:
            grabber = FrameGrabber(session.frames, runner or SubprocessFfmpegRunner())
            return grabber.get_frames, grabber.describe()
        case ToolName.CHECK_SRT if session.check_srt is not None:
            checker = srt_check.SrtChecker(session.check_srt)
            return checker.check_srt, srt_check.DESCRIPTION
    raise AssertionError(f"{name} is allowed but has no config")  # pragma: no cover
