"""Stand-in grill MCP server for the live agent tests.

Serves one `get_frames` tool that always returns a solid green PNG, so a live
test checks that MCP image content reaches the model without needing a video
or ffmpeg. The runner's `--session <tools.json>` argument is ignored.
"""

from __future__ import annotations

import struct
import zlib

from mcp.server.fastmcp import FastMCP, Image

GREEN = (0, 170, 0)

server = FastMCP("grill")


@server.tool()
def get_frames(times: list[float]) -> Image:
    """Return the video frame at the given timestamps (seconds) as an image."""
    return Image(data=solid_png(GREEN), format="png")


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
    server.run()
