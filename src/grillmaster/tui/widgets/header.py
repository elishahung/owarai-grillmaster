"""Top bar: project, serial position, weighted progress, clock, ASR cost
and status."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from grillmaster.core.stage_key import StageKey
from grillmaster.events.types import PlanKind
from grillmaster.tui.state import ItemState, SessionState
from grillmaster.tui.widgets._common import bar, fmt_clock, fmt_tokens

if TYPE_CHECKING:
    from rich.console import RenderableType

    from grillmaster.tui.state import PipelineState
    from grillmaster.tui.widgets._common import View


class Header(Static):
    def show(self, state: PipelineState, view: View) -> None:
        self.update(render_header(state, view))


def render_header(state: PipelineState, view: View) -> RenderableType:
    title = Text.assemble(
        ("Owarai GrillMaster", "bold magenta"),
        ("  ·  project ", "grey58"),
        (state.project or "…", "bold"),
    )
    if state.batch is not None:
        title.append("  ·  serial ", "grey58")
        title.append(f"{state.batch[0]}/{state.batch[1]}", "bold yellow1")

    stages = [step for step in state.steps if step.kind is PlanKind.STAGE]
    done = sum(step.state in {ItemState.DONE, ItemState.CACHED} for step in stages)
    planned = sum(
        step.state not in {ItemState.DISABLED, ItemState.SKIPPED} for step in stages
    )
    if state.failed:
        status = Text("FAILED", "bold red")
    elif state.finished:
        status = Text("COMPLETED", "bold green")
    else:
        status = Text("RUNNING", "bold cyan")

    progress = state.total_progress()
    info = Text()
    info.append(f"{progress * 100:3.0f}%", "bold")
    info.append(
        f"  stage {done}/{planned}  ·  elapsed {fmt_clock(state.wall_elapsed())}"
    )
    asr = state.step(StageKey.ASR)
    if asr is not None and asr.result:
        # The ASR stage reports its spend and the project's total as its result.
        info.append("  ·  ASR ")
        info.append(asr.result, "green")
    running = sum(
        session.state is SessionState.RUNNING for session in state.sessions.values()
    )
    if state.sessions:
        usage = state.total_usage()
        tokens = usage["input_tokens"] + usage["output_tokens"]
        info.append(f"  ·  agents {running} running  ·  tokens ")
        info.append(fmt_tokens(tokens), "green")
    info.append("  ·  ")
    info.append(
        Text("follow", "green") if view.follow else Text("follow off", "grey42")
    )
    line = Table.grid(padding=(0, 2))
    for _ in range(3):
        line.add_column()
    line.add_row(bar(progress, width=40), info, status)
    return Group(title, Text(), line)
