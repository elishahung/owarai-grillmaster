"""`grill reset <id> --from/--only <stage>`."""

from __future__ import annotations

from typing import Annotated

import typer

from grillmaster.cli.common import (
    fail,
    hold_project_or_exit,
    load_or_exit,
    locate_project_or_exit,
)
from grillmaster.core.stage_key import StageKey


def reset_command(
    project: Annotated[
        str,
        typer.Argument(help="Project ID, URL or directory.", show_default=False),
    ],
    *,
    from_: Annotated[
        StageKey | None,
        typer.Option(
            "--from",
            help="Clear this stage and every later one.",
            show_default=False,
        ),
    ] = None,
    only: Annotated[
        StageKey | None,
        typer.Option(
            "--only",
            help="Clear only this stage; later stages keep their results.",
            show_default=False,
        ),
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")
    ] = False,
) -> None:
    """Forget stage results (ledger, work directory, outputs) so they run again.

    Only local projects (under `projects/`) can be reset: an archived one can
    no longer run stages.
    """
    from grillmaster.pipeline.reset import reset, stages_from
    from grillmaster.project.errors import ArchivedProjectError

    if from_ is not None and only is None:
        keys, scope = stages_from(from_), f"{from_} and every later stage"
    elif only is not None and from_ is None:
        keys, scope = (only,), f"only {only}"
    else:
        fail("Pass exactly one of --from or --only")
    loaded = load_or_exit()
    location, found = locate_project_or_exit(project, loaded)
    layout = location.layout
    if not location.local:
        fail(str(ArchivedProjectError(found.id, layout.root)))
    with hold_project_or_exit(loaded, layout, found.id) as state:
        if not yes:
            typer.confirm(
                f"Reset {scope} of {state.id}? Their outputs are deleted.", abort=True
            )
        result = reset(layout, state, keys)
    typer.echo(f"Reset {', '.join(result.stages)} of {state.id}")
    for path in result.removed:
        typer.echo(f"  removed {path}")
