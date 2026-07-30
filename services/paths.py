"""Platform path-length limits for generated directory names.

Deliverable directory names embed the source video title, which is unbounded
(a YouTube title alone reaches 100 characters). Windows still enforces the
classic 260-character MAX_PATH for every process that is not long-path aware —
including the ffmpeg and yt-dlp binaries this pipeline shells out to — so
generated names are trimmed up front instead of failing halfway through an
archive move.
"""

from __future__ import annotations

import os
from pathlib import Path

from loguru import logger

# Windows MAX_PATH counts the terminating NUL, leaving 259 usable units.
# POSIX PATH_MAX is 4096, which titles never approach.
MAX_PATH_UNITS = 259 if os.name == "nt" else 4096

# NTFS and the common POSIX filesystems both cap a single component at 255
# units — a ~85-character Japanese title already reaches it on ext4, where the
# unit is a UTF-8 byte.
MAX_COMPONENT_UNITS = 255

# Trailing characters that make a directory name look truncated, or that
# Windows silently strips (dots and spaces).
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
    separator: str = "_",
) -> str:
    """Build a directory name under `parent` that leaves room for its contents.

    `keep` is the identity part of the name and is never shortened; `tail` is
    the descriptive part, trimmed from the right — and dropped entirely when
    there is no room at all — so that `parent/<name>` plus `reserve` units of
    nested content still fits the platform path limit.

    Args:
        parent: Directory the name will be created in. Resolved against the
            working directory before measuring, since a relative path would
            otherwise understate the real length.
        keep: Identity prefix, kept verbatim.
        tail: Descriptive suffix, trimmed as needed.
        reserve: Units to leave for the longest relative path written inside
            the directory, counting its leading separator.
        separator: Joins `keep` and `tail`.

    Returns:
        The fitted directory name (a bare component, not a path).
    """
    parent_units = measure(os.path.abspath(str(parent)))
    budget = min(
        MAX_COMPONENT_UNITS,
        MAX_PATH_UNITS - parent_units - 1 - reserve,
    )

    if measure(keep) > budget:
        # The root itself is too deep: nothing left to trim without losing the
        # project identity, so keep it and let the filesystem have the last
        # word — with a log line pointing at the configured root.
        logger.warning(
            f"Path limit leaves no room for the directory name {keep!r} under "
            f"{parent} (limit {MAX_PATH_UNITS} units); "
            "consider a shorter ARCHIVED_PATH/PACKAGE_PATH"
        )
        return keep

    candidate = f"{keep}{separator}{tail}" if tail else keep
    if measure(candidate) <= budget:
        return candidate

    room = budget - measure(keep) - measure(separator)
    trimmed = (
        _truncate_to_units(tail, room).rstrip(_TRAILING_NAME_CHARS)
        if room > 0
        else ""
    )
    fitted = f"{keep}{separator}{trimmed}" if trimmed else keep
    logger.info(
        f"Shortened directory name for {parent}: {candidate!r} -> {fitted!r}"
    )
    return fitted


def _truncate_to_units(text: str, limit: int) -> str:
    """Drop characters from the right until `text` measures within `limit`."""
    truncated = text
    while truncated and measure(truncated) > limit:
        truncated = truncated[:-1]
    return truncated
