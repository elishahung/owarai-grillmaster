"""Stand-in grill MCP server for the live agent tests.

Serves one `get_frames` tool that always yields a solid green PNG, so a live
test checks that a frame reaches the model without needing a video or
ffmpeg. Like the real tool it honours the manifest's `pending_frames`: the
frame is then saved under `frames_dir` and left for the runner to attach to
the next message instead of being returned.
"""

from __future__ import annotations

import argparse
import struct
import zlib
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

from grillmaster.agent_tools.frames import PENDING_NOTE
from grillmaster.core.tool_session import ToolSession, leave_pending_frames

GREEN = (0, 170, 0)

server = FastMCP("grill")
_session: ToolSession | None = None


@server.tool(structured_output=False)
def get_frames(times: list[float]) -> Image | str:
    """Return the video frame at the given timestamps (seconds) as an image."""
    frames = _session.frames if _session is not None else None
    if frames is None or frames.pending_frames is None:
        return Image(data=solid_png(GREEN), format="png")
    frames.frames_dir.mkdir(parents=True, exist_ok=True)
    frame = frames.frames_dir / "frame_green.png"
    frame.write_bytes(solid_png(GREEN))
    leave_pending_frames(frames.pending_frames, [frame])
    return f"{len(times)} frame(s) extracted:\n{frame}\n{PENDING_NOTE}"


def solid_png(rgb: tuple[int, int, int], size: int = 64) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * size
    pixels = zlib.compress(row * size)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", pixels)
        + chunk(b"IEND", b"")
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", type=Path, required=True)
    _session = ToolSession.load(parser.parse_args().session)
    server.run()
