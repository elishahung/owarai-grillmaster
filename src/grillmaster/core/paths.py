"""Platform path-length limits for generated directory names.

Deliverable directory names embed the source video title, which is unbounded
(a YouTube title alone reaches 100 characters). Windows still enforces the
classic 260-character MAX_PATH for every process that is not long-path aware,
including the ffmpeg and yt-dlp binaries this pipeline shells out to, so
generated names are trimmed up front instead of failing halfway through an
archive move.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from pathlib import Path

# Windows MAX_PATH counts the terminating NUL, leaving 259 usable units.
# POSIX PATH_MAX is 4096, which titles never approach.
MAX_PATH_UNITS = 259 if os.name == "nt" else 4096

# NTFS and the common POSIX filesystems both cap a single component at 255
# units; a ~85-character Japanese title already reaches it on ext4, where the
# unit is a UTF-8 byte.
MAX_COMPONENT_UNITS = 255

# Trailing characters that make a name look truncated, or that Windows
# silently strips (dots and spaces).
_TRAILING_NAME_CHARS = " ._-"


def measure(text: str) -> int:
    """Length of `text` in the units the platform counts against its limits.

    Windows counts UTF-16 code units; POSIX filesystems count UTF-8 bytes.
    """
    if os.name == "nt":
        return len(text.encode("utf-16-le")) // 2
    return len(text.encode("utf-8"))


def fit_dir_name(
    *,
    parent: Path,
    keep: str,
    tail: str,
    reserve: int,
    max_path_units: int = MAX_PATH_UNITS,
) -> str:
    """Build a directory name under `parent` that leaves room for its contents.

    `keep` is the identity part and is never shortened; `tail` is the
    descriptive part, trimmed from the right (dropped entirely when there is
    no room) so that `parent/<name>` plus `reserve` units of nested content
    still fits `max_path_units`. `parent` is made absolute before measuring,
    since a relative path would understate the real length. `reserve` counts
    the leading separator of the longest relative path written inside.

    Returns a bare component, not a path.
    """
    parent_units = measure(os.path.abspath(parent))  # noqa: PTH100 - no symlink resolution
    budget = min(MAX_COMPONENT_UNITS, max_path_units - parent_units - 1 - reserve)

    if measure(keep) > budget:
        # The root itself is too deep: nothing can be trimmed without losing
        # the identity, so keep it and let the filesystem have the last word.
        logger.warning(
            f"Path limit leaves no room for the directory name {keep!r} under "
            f"{parent} (limit {max_path_units} units); "
            "consider a shorter archive/package root"
        )
        return keep

    candidate = f"{keep}_{tail}" if tail else keep
    if measure(candidate) <= budget:
        return candidate

    room = budget - measure(keep) - 1  # the `_` separator
    trimmed = (
        _truncate_to_units(tail, room).rstrip(_TRAILING_NAME_CHARS) if room > 0 else ""
    )
    fitted = f"{keep}_{trimmed}" if trimmed else keep
    logger.info(f"Shortened directory name for {parent}: {candidate!r} -> {fitted!r}")
    return fitted


def _truncate_to_units(text: str, limit: int) -> str:
    """Drop characters from the right until `text` measures within `limit`."""
    truncated = text
    while truncated and measure(truncated) > limit:
        truncated = truncated[:-1]
    return truncated
