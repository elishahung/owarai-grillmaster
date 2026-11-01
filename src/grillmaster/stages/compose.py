"""Compose delivery step: `video.cht.ass` beside the video, the dialogue of
`video.cht.srt` with the translated telops (`.subs/telop.cht.json`) and the
notes (`.subs/notes.cht.json`) the project has, one track for players.

A delivery step, not a stage: it runs on every complete run (after the
archive move, before packaging), after `grill telop` and `grill notes`, and
in `grill package`, so a hand edit of either JSON reaches it there; a reset
of any stage removes it until then. `--no-telop` / `--no-notes` leave that
layer out, and an unreadable JSON is left out with a warning, as packaging
does with the burn-in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.json_artifact import load_model
from grillmaster.core.srt import read_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.notes.annotate import Notes
from grillmaster.package.compose import write_composed
from grillmaster.stages.base import DeliveryStepDef, require
from grillmaster.telop.select import TelopSubtitles

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from grillmaster.project.layout import ProjectLayout
    from grillmaster.stages.base import StageContext

KEY = "compose"


def _run(ctx: StageContext) -> str:
    layout = ctx.layout
    options = ctx.options
    dialogue = read_srt_file(require(layout.cht_srt, StageKey.FINALIZE))
    telops = (
        load_model(layout.telop_cht_json, TelopSubtitles)
        if options.telop is not False
        else None
    )
    notes = (
        load_model(layout.notes_cht_json, Notes) if options.notes is not False else None
    )
    write_composed(layout.cht_ass, dialogue, telops=telops, notes=notes)
    parts = [f"{len(dialogue)} lines"]
    if telops is not None:
        parts.append(f"{len(telops.items)} telops")
    if notes is not None:
        parts.append(f"{len(notes.items)} notes")
    logger.success(f"Composed {layout.cht_ass}: {', '.join(parts)}")
    return ", ".join(parts)


def _workdir(layout: ProjectLayout) -> Path:
    # Nothing is written there; it exists only on first use.
    return layout.package_work_dir


def _outputs(layout: ProjectLayout) -> Sequence[Path]:
    return (layout.cht_ass,)


STEP = DeliveryStepDef(
    key=KEY,
    label="Compose subtitles",
    weight=1,
    run=_run,
    workdir=_workdir,
    outputs=_outputs,
)
