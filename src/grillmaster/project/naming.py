"""Deliverable names and the archive/package destinations built from them.

A deliverable is named `YYMMDD_<id>_<name>` (`<id>_<name>` when undated). The
`YYMMDD_<id>` stem is its identity and is never shortened; the yt-dlp file
name tail is trimmed so the destination plus everything later written inside
it still fits the platform path limit (`core.paths`).

Both destinations are built under a staging name (`core.fs.staged_dir`)
before they are swapped in, so the budget counts that longer name.

`PROJECT_INNER_PATH_RESERVE` is derived from `ProjectLayout` (its paths and
its session/frames directory enumerations) rather than hand-counted, so a
deeper layout path raises the reserve automatically.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from grillmaster.core.fs import BACKUP_SUFFIX, STAGING_SUFFIX
from grillmaster.core.paths import attempt_path, fit_dir_name, measure
from grillmaster.project.layout import ProjectLayout, session_dir

if TYPE_CHECKING:
    from collections.abc import Iterator

    from grillmaster.project.state import ProjectState

# Widest realistic values for the parameterized parts of the layout: 4-digit
# chunk indexes (`chunk_range_name` pads to 4), a ninth retry, and the frame
# cache name `frame_{ts:010.3f}_{max_side}.jpg` with a 4-digit max side.
SAMPLE_CHUNK = (1234, 5678)
SAMPLE_ATTEMPT = 9
SAMPLE_CHAT_BATCH = 1234
SAMPLE_FRAME = "frame_0001234.567_1024.jpg"
SAMPLE_STAMP = datetime(2026, 12, 31, 23, 59, 59).astimezone()

# The longest file name in an agent session record (see `agents` session
# records).
_LONGEST_SESSION_FILE = "result.json"

# Deliverable names are `<YYMMDD>_<id>[_<name>]`; dated archive groups are
# `YY/MM`, undated ones `etc`. `archived_candidates` matches the same pieces.
_NAME_SEPARATOR = "_"
_STEM_DATE_FORMAT = "%y%m%d"
_STEM_DATE_DIGITS = len(f"{date(2000, 1, 1):{_STEM_DATE_FORMAT}}")
_GROUP_FORMATS = ("%y", "%m")
_UNDATED_GROUP = "etc"
# Sibling directories `core.fs.staged_dir` leaves, never a project.
_NOT_PROJECT_SUFFIXES = (STAGING_SUFFIX, BACKUP_SUFFIX)

# Room (counting the leading separator) for a relative path an agent creates
# under one of `ProjectLayout.agent_workspaces`, e.g. `/notes/candidates.md`.
AGENT_FILE_ALLOWANCE = 48


def deepest_project_paths(layout: ProjectLayout) -> tuple[Path, ...]:
    """The longest paths a project directory can contain, per subtree."""
    # Only chat translation labels its sessions, but applying its longest
    # label to every parent is a safe upper bound.
    label = layout.chat_batch(SAMPLE_CHAT_BATCH).stem
    return (
        *(frames / SAMPLE_FRAME for frames in layout.frames_dirs(SAMPLE_CHUNK)),
        *(
            attempt_path(session_dir(parent, label=label), SAMPLE_ATTEMPT)
            / _LONGEST_SESSION_FILE
            for parent in layout.session_parents(SAMPLE_CHUNK)
        ),
        *(
            workspace / ("x" * (AGENT_FILE_ALLOWANCE - 1))
            for workspace in layout.agent_workspaces
        ),
        layout.chunk_translation(*SAMPLE_CHUNK),
        layout.chunk_audio(*SAMPLE_CHUNK),
        layout.chat_batch(SAMPLE_CHAT_BATCH),
        layout.events_log(SAMPLE_STAMP),
        layout.run_log(SAMPLE_STAMP),
    )


def inner_path_units(layout: ProjectLayout, path: Path) -> int:
    """Units `path` adds below the project root, counting its leading separator."""
    return 1 + measure(str(path.relative_to(layout.root)))


def _project_inner_path_reserve() -> int:
    layout = ProjectLayout(Path("project"))
    return max(inner_path_units(layout, path) for path in deepest_project_paths(layout))


# Path units to keep free inside a project directory for its own contents.
PROJECT_INNER_PATH_RESERVE = _project_inner_path_reserve()


def deliverable_stem(state: ProjectState) -> str:
    """`YYMMDD_<id>`, or `<id>` when the broadcast date is unknown."""
    aired = state.effective_broadcast_date
    if aired is None:
        return state.id
    return f"{aired:{_STEM_DATE_FORMAT}}{_NAME_SEPARATOR}{state.id}"


def deliverable_name(state: ProjectState) -> str:
    """The untrimmed deliverable name; destinations may shorten the tail."""
    stem = deliverable_stem(state)
    return f"{stem}{_NAME_SEPARATOR}{state.name}" if state.name else stem


def archive_group(state: ProjectState) -> Path:
    """Shared parents under the archive root: `YY/MM`, or `etc` when undated."""
    aired = state.effective_broadcast_date
    if aired is None:
        return Path(_UNDATED_GROUP)
    return Path(*(f"{aired:{part}}" for part in _GROUP_FORMATS))


def _fit_deliverable_dir(state: ProjectState, parent: Path, reserve: int) -> Path:
    """`parent/<deliverable name>`, trimmed so `reserve` units still fit below
    its staging name (`<name>.partial`)."""
    return parent / fit_dir_name(
        parent=parent,
        keep=deliverable_stem(state),
        tail=state.name or "",
        reserve=len(STAGING_SUFFIX) + reserve,
    )


def archive_destination(state: ProjectState, archived_root: Path) -> Path:
    """Where the whole project directory moves when archived."""
    return _fit_deliverable_dir(
        state, archived_root / archive_group(state), PROJECT_INNER_PATH_RESERVE
    )


def package_destination(
    state: ProjectState, package_root: Path, *, reserve: int
) -> Path:
    """The deliverable folder, flat under `package_root`.

    `reserve` is the room the caller keeps for the folder's own entries
    (counting the leading separator); the `package` package passes its
    inner-path reserve.
    """
    return _fit_deliverable_dir(state, package_root, reserve)


def archived_candidates(archived_root: Path, video_id: str) -> Iterator[Path]:
    """Directories under `archived_root` whose name could be `video_id`'s
    archived project: `YY/MM/YYMMDD_<id>[_<name>]` or `etc/<id>[_<name>]`
    (see `archive_destination`), in name order. Staging and backup
    directories are not candidates. Callers confirm a match by its
    `project.json` (another ID may start with `<id>_`)."""
    name = re.compile(
        rf"(?P<date>\d{{{_STEM_DATE_DIGITS}}}{_NAME_SEPARATOR})?"
        rf"{re.escape(video_id)}(?:{_NAME_SEPARATOR}|$)"
    )
    for entry, dated in _archived_entries(archived_root):
        found = name.match(entry.name)
        if found is not None and (found["date"] is not None) == dated:
            yield Path(entry.path)


def archived_dirs(archived_root: Path) -> Iterator[Path]:
    """Every directory under `archived_root`'s groups that may be an archived
    project, staging and backup directories excluded. Callers confirm each
    by its `project.json`."""
    for entry, _ in _archived_entries(archived_root):
        yield Path(entry.path)


def _archived_entries(archived_root: Path) -> Iterator[tuple[os.DirEntry[str], bool]]:
    """`(entry, dated)` for each non-staging directory in the archive groups,
    in name order."""

    def projects(parent: Path) -> Iterator[os.DirEntry[str]]:
        for entry in _subdirs(parent):
            if not entry.name.endswith(_NOT_PROJECT_SUFFIXES):
                yield entry

    # Dated groups are two levels deep (`_GROUP_FORMATS`: YY, then MM).
    for group in _subdirs(archived_root):
        if group.name == _UNDATED_GROUP:
            for entry in projects(Path(group.path)):
                yield entry, False
        elif group.name.isdigit():
            for month in _subdirs(Path(group.path)):
                if month.name.isdigit():
                    for entry in projects(Path(month.path)):
                        yield entry, True


def _subdirs(path: Path) -> list[os.DirEntry[str]]:
    """The subdirectories of `path` in name order; none when it is missing.
    Any other `OSError` (an unreachable archive) propagates."""
    try:
        with os.scandir(path) as entries:
            return sorted((e for e in entries if e.is_dir()), key=lambda e: e.name)
    except FileNotFoundError:
        return []
