"""The chunk board: one cell per `chunks/<from>-<to>` session, and the
selected chunk's session activity.

Cells exist only for chunks that started a session this run; chunks served
from their cache never do.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text
from textual.widgets import Static

from grillmaster.tui.widgets._common import (
    CHUNK_STYLE,
    SESSION_ICON,
    activity_text,
    fmt_clock,
)

if TYPE_CHECKING:
    from rich.console import RenderableType

    from grillmaster.tui.state import ChunkCell, PipelineState

CELLS_PER_ROW = 9
ACTIVITY_SHOWN = 15


class ChunkBoard(Static):
    """Hidden while the step has no chunk session."""

    def show(self, state: PipelineState, key: str | None, chunk: str | None) -> None:
        cells = state.chunk_cells(key) if key is not None else []
        self.display = bool(cells)
        if cells:
            self.update(render_chunk_board(state, key or "", chunk))


def render_chunk_board(
    state: PipelineState, key: str, chunk: str | None
) -> RenderableType:
    cells = state.chunk_cells(key)
    grid = Text()
    for position, cell in enumerate(cells, start=1):
        label = (
            f"[{position:02d}]" if cell.session.task == chunk else f" {position:02d} "
        )
        grid.append(label, style=CHUNK_STYLE[cell.session.state])
        grid.append(" ")
        if position % CELLS_PER_ROW == 0 and position < len(cells):
            grid.append("\n\n")
    stats = state.chunk_stats(key)
    line = Text()
    line.append(str(stats.done), "bold")
    line.append(f"/{stats.total} done  ·  active {stats.active}  ·  retries ")
    line.append(str(stats.retries), "dark_orange")
    line.append("  ·  failed ")
    line.append(str(stats.failed), "red" if stats.failed else "")
    parts: list[RenderableType] = [grid, Text(), line, Text()]
    selected = next((cell for cell in cells if cell.session.task == chunk), None)
    if selected is None:
        parts.append(Text("←/→ select a chunk to see its session", style="grey42"))
    else:
        parts += _chunk_activity(state, selected)
    parts.append(Text())
    return Group(*parts)


def _chunk_activity(state: PipelineState, cell: ChunkCell) -> list[RenderableType]:
    session = cell.session
    icon, style = SESSION_ICON[session.state]
    head = Text()
    head.append(f"{icon} ", style=style)
    head.append(session.task, style="bold")
    head.append(f"  blocks {cell.from_index}-{cell.to_index}", style="grey58")
    head.append(f"  ·  {session.spec}", style="cyan")
    head.append(f"  ·  {fmt_clock(session.live_elapsed(state.now()))}")
    head.append(f"  ·  tools {session.tool_calls}  ·  repairs {session.repairs}")
    if session.retries:
        head.append(f"  ·  attempt {session.attempts}", style="dark_orange")
    entries = list(session.activity)[-ACTIVITY_SHOWN:]
    if not entries:
        return [head, Text("no activity yet", style="grey42")]
    return [head, *(activity_text(entry) for entry in entries)]
