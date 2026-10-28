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
# unit is a UTF-8 byte. The component limit belongs to the destination
# filesystem, not the client: an SMB share backed by ext4 or btrfs counts
# UTF-8 bytes even from Windows, so components are measured by
# `component_units`.
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


def component_units(text: str) -> int:
    """Length of a single path component against `MAX_COMPONENT_UNITS`.

    UTF-8 bytes, which are never fewer than UTF-16 code units, so a name
    that fits here fits NTFS and the POSIX filesystems behind a network share
    alike.
    """
    return len(text.encode("utf-8"))


def handoff_path(first: Path) -> Path:
    """Where the handoff of something first written at `first` goes: the
    sibling `<name>_fix` (agent session records of a task's handoff model)."""
    return first.with_name(f"{first.name}_fix")


def attempt_path(first: Path, attempt: int) -> Path:
    """Where attempt `attempt` (1-based) of something first written at
    `first` goes: `first` itself, then siblings `<name>.2`, `<name>.3`, ...
    (agent session records per retry)."""
    if attempt < 1:
        raise ValueError(f"Attempts are 1-based: {attempt}")
    return first if attempt == 1 else first.with_name(f"{first.name}.{attempt}")


def fit_dir_name(
    *,
    parent: Path,
    keep: str,
    tail: str,
    reserve: int,
    suffix: str = "",
    max_path_units: int = MAX_PATH_UNITS,
) -> str:
    """Build a directory name under `parent` that leaves room for its contents.

    `keep` is the identity part and is never shortened; `tail` is the
    descriptive part, trimmed from the right (dropped entirely when there is
    no room) so that `parent/<name><suffix>` stays within the component limit
    and, plus `reserve` units of nested content, still fits `max_path_units`.
    `suffix` is the longest sibling name the caller derives from the result
    (a staging name). `parent` is made absolute before measuring, since a
    relative path would understate the real length. `reserve` counts the
    leading separator of the longest relative path written inside.

    Returns a bare component, without `suffix`, not a path.
    """
    parent_units = measure(os.path.abspath(parent))  # noqa: PTH100 - no symlink resolution
    path_room = max_path_units - parent_units - 1 - measure(suffix) - reserve
    component_room = MAX_COMPONENT_UNITS - component_units(suffix)

    def fits(name: str) -> bool:
        return measure(name) <= path_room and component_units(name) <= component_room

    if not fits(keep):
        # The root itself is too deep: nothing can be trimmed without losing
        # the identity, so keep it and let the filesystem have the last word.
        logger.warning(
            f"Path limit leaves no room for the directory name {keep!r} under "
            f"{parent} (limit {max_path_units} units); "
            "consider a shorter archive/package root"
        )
        return keep

    candidate = f"{keep}_{tail}" if tail else keep
    if fits(candidate):
        return candidate

    # Drop characters from the right until the whole name fits.
    trimmed = tail
    while trimmed and not fits(f"{keep}_{trimmed}"):
        trimmed = trimmed[:-1]
    trimmed = trimmed.rstrip(_TRAILING_NAME_CHARS)
    fitted = f"{keep}_{trimmed}" if trimmed else keep
    logger.info(f"Shortened directory name for {parent}: {candidate!r} -> {fitted!r}")
    return fitted
