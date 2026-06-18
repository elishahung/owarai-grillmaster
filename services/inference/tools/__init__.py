"""Agent-facing helper tools that live alongside the inference backends.

``get_frames.py`` is a standalone CLI the agent backends run to pull video
frames at specific timestamps on demand; ``build_frame_tool_instruction``
renders the system-prompt block that teaches an agent backend when and how to
call it. The instruction is appended only for agent backends (gemini-cli /
codex / claude); gemini-api never sees it, so its prompt stays byte-stable.
"""

from __future__ import annotations

import sys
from pathlib import Path

FRAME_TOOL_SCRIPT: Path = (Path(__file__).parent / "get_frames.py").resolve()

# Mirror of get_frames.py `_MAX_FRAMES_PER_CALL`, surfaced in the instruction so
# the agent knows the per-call cap.
_MAX_FRAMES_PER_CALL = 6

__all__ = ["FRAME_TOOL_SCRIPT", "build_frame_tool_instruction"]


def build_frame_tool_instruction(
    video_path: Path,
    start_seconds: float,
    end_seconds: float,
    *,
    scope_label: str,
) -> str:
    """Render the on-demand frame-tool instruction for an agent backend.

    Embeds the exact command (current interpreter + absolute script and video
    paths) and the valid time window, so the agent can run it verbatim from its
    throwaway working directory.
    """
    python = Path(sys.executable).resolve()
    video = video_path.resolve()
    start = max(0.0, start_seconds)
    end = max(start, end_seconds)
    return (
        "## On-demand video frames\n"
        "The pre-sampled reference images may not cover the exact moment you "
        "need. When a line is doubtful — garbled or suspicious ASR, an unclear "
        "proper noun or name, an on-screen text card (字卡, which Japanese "
        "variety shows often flash exactly at these moments), or you simply "
        "want to confirm what is on screen — extract the exact frames you need "
        "instead of guessing.\n\n"
        "Run this command with the specific timestamps (in seconds) you want to "
        "see; it prints absolute image paths, one per line, which you then open "
        "with your file/image-reading tool:\n\n"
        f'```\n"{python}" "{FRAME_TOOL_SCRIPT}" --video "{video}" '
        '--times "62.5,70,77"\n```\n\n'
        f"- Valid timestamps for {scope_label}: {start:.3f}s to {end:.3f}s. "
        "Stay strictly within this window.\n"
        f"- At most {_MAX_FRAMES_PER_CALL} timestamps per call.\n"
        "- Frames are written to a temporary directory outside the project, so "
        "running this never counts as modifying project files.\n"
        "- Use this sparingly — only where seeing the frame would actually "
        "change a translation decision."
    )
