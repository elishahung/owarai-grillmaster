"""Deliverable names and the archive/package destinations built from them.

A deliverable is named `YYMMDD_<id>_<name>` (`<id>_<name>` when undated). The
`YYMMDD_<id>` stem is its identity and is never shortened; the yt-dlp file
name tail is trimmed so the destination plus everything later written inside
it still fits the platform path limit (`core.paths`).

`PROJECT_INNER_PATH_RESERVE` is derived from `ProjectLayout` (its paths and
its session/frames directory enumerations) rather than hand-counted, so a
deeper layout path raises the reserve automatically.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from grillmaster.core.paths import fit_dir_name, measure
from grillmaster.project.layout import ProjectLayout, session_dir

if TYPE_CHECKING:
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
            session_dir(parent, attempt=SAMPLE_ATTEMPT, label=label)
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
    if state.broadcast_date is None:
        return state.id
    return f"{state.broadcast_date:%y%m%d}_{state.id}"


def deliverable_name(state: ProjectState) -> str:
    """The untrimmed deliverable name; destinations may shorten the tail."""
    stem = deliverable_stem(state)
    return f"{stem}_{state.name}" if state.name else stem


def archive_group(state: ProjectState) -> Path:
    """Shared parents under the archive root: `YY/MM`, or `etc` when undated."""
    if state.broadcast_date is None:
        return Path("etc")
    return Path(f"{state.broadcast_date:%y}") / f"{state.broadcast_date:%m}"


def _fit_deliverable_dir(state: ProjectState, parent: Path, reserve: int) -> Path:
    return parent / fit_dir_name(
        parent=parent,
        keep=deliverable_stem(state),
        tail=state.name or "",
        reserve=reserve,
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

    `reserve` is the room the caller keeps for the folder's own entries; the
    `package` package passes its inner-path reserve.
    """
    return _fit_deliverable_dir(state, package_root, reserve)
