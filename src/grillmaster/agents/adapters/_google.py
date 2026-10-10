"""What the two Google subscription CLIs (agy, gemini) share."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.agents.adapters.base import McpServer, TurnRequest

# Metered keys the CLIs would prefer over the cached subscription login.
API_KEY_ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY")
# The grill tool server's name in the CLIs' MCP configuration.
MCP_SERVER = "grill"


def subscription_env() -> dict[str, str]:
    """The environment without `API_KEY_ENV_VARS`."""
    env = dict(os.environ)
    for key in API_KEY_ENV_VARS:
        env.pop(key, None)
    return env


def mcp_servers(mcp: McpServer) -> dict[str, dict[str, object]]:
    """The `mcpServers` entry both CLIs read from their JSON settings."""
    return {MCP_SERVER: {"command": mcp.command, "args": list(mcp.args)}}


def readable_roots(request: TurnRequest) -> list[Path]:
    """The workdir first, then the extra roots and the media folders the CLI
    must be allowed to read, each once (case-insensitively on Windows)."""
    roots = [
        request.workdir,
        *request.add_dirs,
        *(path.parent for path in (*request.images, *request.audio)),
    ]
    unique: dict[str, Path] = {}
    for root in roots:
        unique.setdefault(path_key(str(root)), root)
    return list(unique.values())


def path_key(path: str) -> str:
    """`path` normalized for comparison."""
    return os.path.normcase(os.path.normpath(path))
