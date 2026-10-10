"""Argument handling Typer cannot express on its own."""

from __future__ import annotations

from pathlib import Path

from grillmaster.core.source_id import is_url

# What a bare `--remix` expands to: "the configured default pool". Pool
# names are non-empty, so the empty string cannot collide with one.
DEFAULT_POOL = ""

_REMIX_FLAG = "--remix"
_PARENT_FLAG = "--parent"
# What a bare `--parent` becomes: pick a recent archived project on screen.
PICK_PARENT_FLAG = "--pick-parent"


def expand_bare_options(args: list[str]) -> list[str]:
    """Let `--remix` and `--parent` stand alone.

    Typer has no optional-value options, so a bare flag is rewritten before
    parsing. `--remix` is bare when it is last or followed by another option,
    and gets `DEFAULT_POOL` inserted. `--parent` is bare unless followed by
    a directory or something that reads as a path (a mistyped one still fails
    as a value), so `grill --parent <source>` works too; it becomes
    `PICK_PARENT_FLAG`.
    """
    expanded: list[str] = []
    for index, arg in enumerate(args):
        following = args[index + 1] if index + 1 < len(args) else None
        if arg == _PARENT_FLAG and not _is_parent_value(following):
            expanded.append(PICK_PARENT_FLAG)
            continue
        expanded.append(arg)
        if arg == _REMIX_FLAG and (following is None or following.startswith("-")):
            expanded.append(DEFAULT_POOL)
    return expanded


def _is_parent_value(text: str | None) -> bool:
    if text is None or text.startswith("-"):
        return False
    return Path(text).is_dir() or looks_like_path(text)


def resolve_remix(value: str | None, default_pool: str) -> str | None:
    """The pool a `--remix` value names; `None` when the flag was not given."""
    if value is None:
        return None
    return value or default_pool


def looks_like_path(text: str) -> bool:
    """Whether `text` reads as a filesystem path rather than a video ID or URL.

    A bare ID is never a path, even when a folder in the working directory
    shares its name.
    """
    return not is_url(text) and ("/" in text or "\\" in text)


def reject_directory_source(text: str) -> None:
    """Refuse a local project directory given where a video source belongs."""
    if looks_like_path(text) and Path(text).is_dir():
        raise ValueError(
            f"A local directory is not a video source: {text}. Use a video ID "
            "or URL, or run 'grill package <dir>' to package an existing project."
        )


def parse_section_time(text: str) -> float:
    """Seconds from `90`, `1:30`, `0:01:30` or `1h30m` (yt-dlp's parser)."""
    # yt-dlp is slow to import; only a run with --start/--to needs it.
    from yt_dlp.utils import parse_duration

    seconds = parse_duration(text)
    if seconds is None or seconds < 0:
        raise ValueError(f"Invalid time value: {text!r}")
    return float(seconds)
