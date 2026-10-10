"""Sidebar: every planned step, grouped by kind, with state and timing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.text import Text
from textual.widgets import Static

from grillmaster.events.types import PlanKind
from grillmaster.tui.state import ItemState
from grillmaster.tui.widgets._common import STATE_ICON, fmt_clock

if TYPE_CHECKING:
    from rich.console import RenderableType

    from grillmaster.tui.state import PipelineState, StepView
    from grillmaster.tui.widgets._common import View

SECTION_TITLE = {
    PlanKind.STAGE: "Pipeline",
    PlanKind.DELIVERY: "Delivery",
    PlanKind.SIDE_TASK: "Side tasks",
}


class StageList(Static):
    def show(self, state: PipelineState, view: View) -> None:
        self.update(render_stage_list(state, view))


def render_stage_list(state: PipelineState, view: View) -> RenderableType:
    steps = state.display_steps()
    if not steps:
        return Group(
            Text(SECTION_TITLE[PlanKind.STAGE], style="bold underline"),
            Text(),
            Text("starting…", style="grey42"),
        )
    rows: list[RenderableType] = []
    kind: PlanKind | None = None
    for step in steps:
        if step.kind is not kind:
            if kind is not None:
                rows.append(Text())
            rows += [Text(SECTION_TITLE[step.kind], style="bold underline"), Text()]
            kind = step.kind
        rows.append(_row(state, step, selected=step.key == view.selected))
    return Group(*rows)


def row_line(state: PipelineState, key: str) -> int | None:
    """The content line `render_stage_list` puts step `key` on."""
    line = 0
    kind: PlanKind | None = None
    for step in state.display_steps():
        if step.kind is not kind:
            line += 2 if kind is None else 3
            kind = step.kind
        if step.key == key:
            return line
        line += 1
    return None


def _row(state: PipelineState, step: StepView, *, selected: bool) -> Text:
    icon, style = STATE_ICON[step.state]
    text = Text()
    text.append("❯" if selected else " ", style="bold cyan")  # noqa: RUF001 - selection marker glyph
    text.append(f"{icon} ", style=style)
    if step.state is ItemState.RUNNING:
        name_style = "bold white"
    elif step.state in {ItemState.PENDING, ItemState.DISABLED, ItemState.SKIPPED}:
        name_style = "grey42"
    else:
        name_style = ""
    text.append(step.label, style=name_style)
    if step.state is ItemState.RUNNING:
        stats = state.chunk_stats(step.key)
        if stats.total:
            text.append(f"  {stats.done}/{stats.total}", style="yellow1")
        else:
            text.append(
                f"  {fmt_clock(step.live_elapsed(state.now()))}", style="yellow1"
            )
    elif step.state is ItemState.DONE:
        text.append(f"  {fmt_clock(step.elapsed)}", style="grey58")
    elif step.state is ItemState.CACHED:
        text.append("  cached", style="cyan")
    if selected:
        text.stylize("on grey23")
    return text
