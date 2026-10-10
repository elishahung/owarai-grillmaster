"""`python -m grillmaster.agent_tools --session <tools.json>`: serve over stdio.

Without `--session`, the manifest path comes from `GRILL_TOOL_SESSION`. A
missing or invalid manifest (or an unreadable reference SRT) exits non-zero with the reason on stderr before
any MCP traffic, so the agent CLI reports a failed server instead of an
empty tool list.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from grillmaster.agent_tools.server import build_server
from grillmaster.core.tool_session import SESSION_ENV_VAR, ToolSession

_EXIT_BAD_SESSION = 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m grillmaster.agent_tools")
    parser.add_argument(
        "--session",
        type=Path,
        help=f"Path to the tools.json manifest (default: ${SESSION_ENV_VAR}).",
    )
    args = parser.parse_args(argv)
    path: Path | None = args.session
    if path is None:
        env_value = os.environ.get(SESSION_ENV_VAR)
        if not env_value:
            return _fail(f"no tool session: pass --session or set {SESSION_ENV_VAR}")
        path = Path(env_value)
    try:
        # Building the server reads the session's reference files.
        server = build_server(ToolSession.load(path))
    except (OSError, ValueError) as error:  # pydantic errors are ValueErrors
        return _fail(f"invalid tool session {path}: {error}")
    server.run("stdio")
    return 0


def _fail(message: str) -> int:
    sys.stderr.write(f"grillmaster.agent_tools: {message}\n")
    return _EXIT_BAD_SESSION


if __name__ == "__main__":
    raise SystemExit(main())
