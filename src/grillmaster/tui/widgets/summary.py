"""The end-of-run panel: outcome, each step's result, agent totals."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from grillmaster.tui.widgets._common import fmt_clock, fmt_tokens

if TYPE_CHECKING:
    from rich.console import RenderableType

    from grillmaster.tui.state import PipelineState


class Summary(Static):
    def show(self, state: PipelineState) -> None:
        self.update(render_summary(state))


def render_summary(state: PipelineState) -> RenderableType:
    if state.failed:
        body: list[RenderableType] = [
            Text("✘ Pipeline failed", style="bold red"),
            Text(),
        ]
        if state.error:
            body += [Text(state.error, style="red"), Text()]
        body.append(
            Text(
                "r retries from the failed stage (completed stages are cached) · "
                "select the failed stage for its log · c copies a log.",
                style="grey58",
            )
        )
        return Panel(Group(*body), title="[bold]Failed[/bold]", border_style="red")

    table = Table.grid(padding=(0, 2))
    table.add_column(style="grey58")
    table.add_column(style="green")
    for step in state.display_steps():
        if step.result:
            table.add_row(step.label, step.result)
    usage = state.total_usage()
    repairs = sum(session.repairs for session in state.sessions.values())
    table.add_row("agent sessions", f"{len(state.sessions)} ({repairs} repairs)")
    table.add_row(
        "tokens",
        f"{fmt_tokens(usage['input_tokens'])} in · "
        f"{fmt_tokens(usage['output_tokens'])} out",
    )
    table.add_row("wall time", fmt_clock(state.wall_elapsed()))
    return Panel(
        Group(
            Text("✔ Pipeline completed\n", style="bold green"),
            table,
            Text("\n↑/↓ browse the steps · a shows agent activity", style="grey58"),
        ),
        title="[bold]Summary[/bold]",
        border_style="green",
    )
