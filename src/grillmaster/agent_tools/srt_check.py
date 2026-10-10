"""`check_srt`: skeleton check of a candidate SRT against the session reference."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from grillmaster.core.srt import read_srt_file
from grillmaster.subtitles.structure import check_aligned_file

if TYPE_CHECKING:
    from grillmaster.core.tool_session import SrtCheckTool

VALID = "VALID"

DESCRIPTION = (
    "Check an SRT file you wrote against the reference subtitles: it must keep "
    "the same block count, indexes and timecodes, and no block may be empty. "
    "Pass the file's absolute path. Returns VALID, or INVALID followed by one "
    "problem per line."
)


class SrtChecker:
    """Serves `check_srt` for one session; parses the reference once, up front.

    An unreadable reference is a session error and raises here, when the
    server is built.
    """

    def __init__(self, config: SrtCheckTool) -> None:
        self._reference = read_srt_file(config.reference_srt)

    def check_srt(self, path: str) -> str:
        """`VALID`, or `INVALID` plus the problems, one per line.

        An unreadable or malformed candidate is a problem for the agent to fix.
        """
        # Absolute only: the agent's cwd is unknown to this server process.
        candidate_path = Path(path)
        if not candidate_path.is_absolute():
            return f"INVALID\n- pass an absolute path, got: {path}"
        problems = check_aligned_file(self._reference, candidate_path)
        if not problems:
            return VALID
        return "\n".join(["INVALID", *(f"- {problem}" for problem in problems)])
