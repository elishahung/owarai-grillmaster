"""The selected step: params, state, live progress bars, result or error."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from grillmaster.tui.state import ItemState
from grillmaster.tui.widgets._common import bar, fmt_clock

if TYPE_CHECKING:
    from rich.console import RenderableType

    from grillmaster.tui.state import PipelineState, StepView

BORDER = {
    ItemState.RUNNING: "cyan",
    ItemState.DONE: "green",
    ItemState.CACHED: "cyan",
    ItemState.DISABLED: "grey42",
    ItemState.SKIPPED: "grey42",
    ItemState.PENDING: "grey42",
    ItemState.FAILED: "red",
}


class StageDetail(Static):
    def show(self, state: PipelineState, step: StepView | None) -> None:
        self.update(render_stage_detail(state, step))


def render_stage_detail(state: PipelineState, step: StepView | None) -> RenderableType:
    if step is None:
        return Panel(
            Text("waiting for pipeline…", style="grey42"), border_style="grey42"
        )
    body: list[RenderableType] = []
    if step.params:
        params = Table.grid(padding=(0, 2))
        params.add_column(style="grey58")
        params.add_column(style="cyan")
        for key, value in step.params.items():
            params.add_row(key, value)
        body += [params, Text()]
    match step.state:
        case ItemState.RUNNING:
            body += _running(state, step)
        case ItemState.DONE:
            body.append(
                Text.assemble(
                    ("✔ completed", "green"), f" in {fmt_clock(step.elapsed)}"
                )
            )
        case ItemState.CACHED:
            body.append(Text("✔ already complete (previous run)", style="cyan"))
        case ItemState.FAILED:
            body.append(Text("✘ failed", style="bold red"))
            if step.error:
                body += [Text(), Text(step.error, style="red")]
        case ItemState.DISABLED:
            body.append(Text("disabled for this run", style="grey42"))
        case ItemState.SKIPPED:
            body.append(Text("skipped: the run stops at --break-after", style="grey42"))
        case ItemState.PENDING:
            body.append(Text("waiting…", style="grey42"))
    if step.result:
        body += [Text(), Text.assemble(("result  ", "grey58"), (step.result, "bold"))]
    return Panel(
        Group(*body),
        title=f"[bold]{step.label}[/bold]",
        border_style=BORDER[step.state],
    )


def _running(state: PipelineState, step: StepView) -> list[RenderableType]:
    body: list[RenderableType] = []
    for progress in step.bars.values():
        if progress.done:
            continue
        caption = Text(progress.label, style="grey58")
        if progress.note:
            caption.append(f"  {progress.note}", style="grey42")
        body.append(caption)
        fraction = progress.fraction
        if fraction is None:
            body.append(Text("working…", style="yellow1"))
            continue
        row = Table.grid(padding=(0, 1))
        row.add_column()
        row.add_column(width=8)
        row.add_row(bar(fraction, width=40), Text(f"{fraction * 100:3.0f}%"))
        body.append(row)
    body += [
        Text(),
        Text(f"{fmt_clock(step.live_elapsed(state.now()))} elapsed", style="grey58"),
    ]
    return body
