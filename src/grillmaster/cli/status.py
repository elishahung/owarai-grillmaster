"""`grill status [<id>]`: the stage ledger of one project, or every project."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from grillmaster.cli.common import load_project_or_exit, projects_root_or_exit
from grillmaster.core.stage_key import StageKey
from grillmaster.core.timecode import format_elapsed

if TYPE_CHECKING:
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState, TaskRecord


def status_command(
    project: Annotated[
        str | None,
        typer.Argument(
            help="Project ID, URL or directory; omit to list every project.",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Show which stages are done, with their models, timings and ASR cost."""
    if project is None:
        _list_projects(projects_root_or_exit())
        return
    layout, state = load_project_or_exit(project)
    for line in describe_project(layout, state):
        typer.echo(line)


def describe_project(layout: ProjectLayout, state: ProjectState) -> list[str]:
    from grillmaster.pipeline.registry import PIPELINE

    section = state.section
    lines = [
        f"{state.id} ({state.platform})  {state.name or '(unnamed)'}",
        f"  directory      {layout.root}",
        f"  broadcast date {state.broadcast_date or 'unknown'}",
        f"  ASR cost       ${state.asr_cost_usd:.4f}",
    ]
    if state.parent is not None:
        lines.append(f"  parent         {state.parent}")
    if section.is_cut:
        end = "end" if section.end is None else f"{section.end:g}s"
        lines.append(f"  section        {section.start or 0:g}s - {end}")
    lines.append("")
    for key in StageKey:
        record = state.stages.get(key)
        stage = PIPELINE.stage(key)
        label = stage.label if stage is not None else key.value
        head = f"  {key.number:02d} {key.value:<15} {label:<28}"
        if record is None:
            lines.append(f"{head} -")
            continue
        details = [_timing(record)]
        details += [f"{name}={value}" for name, value in record.params.items()]
        if record.agent_usage:
            details.append(_usage(record.agent_usage))
        lines.append(f"{head} done  {'  '.join(details)}")
    lines.append("")
    side = state.side_tasks
    lines.append(f"  cover          {_timing(side.cover) if side.cover else '-'}")
    research = side.date_research
    lines.append(
        "  date research  "
        + (f"{research.verdict}  {_timing(research)}" if research else "-")
    )
    return lines


def _timing(record: TaskRecord) -> str:
    return f"{record.completed_at:%Y-%m-%d %H:%M} ({format_elapsed(record.elapsed_s)})"


def _usage(usage: Mapping[str, int]) -> str:
    return "tokens " + ",".join(f"{name}={count}" for name, count in usage.items())


def _list_projects(projects_root: Path) -> None:
    """One line per project directory under `projects_root`: id, stages done,
    name. An unreadable `project.json` is marked, not fatal."""
    from grillmaster.project.errors import ProjectNotFoundError
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.store import load_summary

    roots = sorted(projects_root.iterdir()) if projects_root.is_dir() else []
    total = len(StageKey)
    listed = 0
    for root in roots:
        if not root.is_dir():
            continue
        try:
            summary = load_summary(ProjectLayout(root))
        except ProjectNotFoundError:
            continue
        except ValueError as error:
            reason = str(error).splitlines()[0]
            typer.echo(
                f"{root.name:<16}  ?/{total}  (unreadable project.json: {reason})"
            )
        else:
            done = f"{len(summary.stages):>2}/{total}"
            typer.echo(f"{summary.id:<16} {done}  {summary.name or '(unnamed)'}")
        listed += 1
    if not listed:
        typer.echo(f"No projects in {projects_root}")
