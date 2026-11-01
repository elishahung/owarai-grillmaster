"""Notes stage, the last one: once the dialogue subtitles and the translated
telops are final, the editor agent writes translator's notes for what a
Taiwanese viewer is missing, into `.subs/notes.cht.json`, which packaging
burns in at the top of the picture and the compose delivery step adds to
`video.cht.ass`.

Whether it runs is the most specific explicit choice: `--notes` /
`--no-notes`, then the program's `notes`, then `[features] notes`. It reads
`video.cht.srt` beside `ja.srt`, the translated telops when there are
any, the kept telop scan (its cards as evidence; no OCR runs for the
notes), the broadcast date (date research's verdict too), the effective briefing
and the glossary-check and proofread reports. `.work/16_notes/` keeps the
session and the fetched frames. The deliverable is hand-editable; `grill
reset <id> --only notes` writes the notes again. `grill notes` runs this
stage on a finished or archived project whatever its ledger says, and
`grill notes --probe` asks the agent, without any research, what it would
note and why (`.work/16_notes/probe.json`, `probe.md`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.briefing import Briefing
from grillmaster.core.fs import atomic_write_text
from grillmaster.core.json_artifact import load_model, read_model, write_model
from grillmaster.core.model_spec import Role
from grillmaster.core.srt import read_srt_file
from grillmaster.core.stage_key import SideTaskKey, StageKey
from grillmaster.core.tool_session import ToolSession
from grillmaster.notes.annotate import (
    NotesInputs,
    build_notes_task,
    build_probe_task,
    render_probe,
    to_notes,
)
from grillmaster.notes.errors import NotesError
from grillmaster.stages import _telop
from grillmaster.stages._common import (
    frames_tool,
    program_rules,
    role_params,
    switch_applies,
    switch_enabled,
)
from grillmaster.stages.base import DeliveryStepDef, StageDef, require
from grillmaster.subtitles.structure import check_aligned
from grillmaster.telop.reference import render_reference
from grillmaster.telop.select import TelopSubtitles

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from grillmaster.project.layout import ProjectLayout
    from grillmaster.stages.base import StageContext


def _inputs(ctx: StageContext) -> NotesInputs:
    """The agent's inputs."""
    layout = ctx.layout
    transcript = read_srt_file(require(layout.ja_srt, StageKey.TRANSCRIPT))
    subtitles = read_srt_file(require(layout.cht_srt, StageKey.FINALIZE))
    if problems := check_aligned(transcript, subtitles):
        raise NotesError(
            f"{layout.cht_srt} does not pair with {layout.ja_srt} block by "
            f"block: {'; '.join(problems)}"
        )
    telops = load_model(layout.telop_cht_json, TelopSubtitles) or TelopSubtitles()
    cards = _telop.kept_cards(ctx)
    # Its verdict dates the current events the talk means.
    ctx.side_tasks.wait(SideTaskKey.DATE_RESEARCH)
    reports = tuple(
        (name, path.read_text(encoding="utf-8"))
        for name, path in (
            ("glossary check", layout.glossary_report),
            ("proofread", layout.proofread_report),
        )
        if path.exists()
    )
    return NotesInputs(
        broadcast_date=ctx.state.effective_broadcast_date,
        transcript=transcript,
        subtitles=subtitles,
        telops=telops,
        on_screen=None if cards is None else render_reference(cards.offered),
        briefing=read_model(
            require(layout.effective_briefing(), StageKey.PREPASS), Briefing
        ),
        reports=reports,
        program_instruction=program_rules(
            ctx.config, ctx.state.source
        ).instruction_text(StageKey.NOTES),
    )


def _tools(ctx: StageContext) -> ToolSession:
    return ToolSession(
        project_root=ctx.layout.root,
        frames=frames_tool(ctx, ctx.layout.notes_frames_dir),
        check_srt=None,
        watch_clip=None,
    )


def _run(ctx: StageContext) -> str:
    layout = ctx.layout
    inputs = _inputs(ctx)
    answer = ctx.agents.run(
        build_notes_task(inputs, session_dir=ctx.session_dir(), tools=_tools(ctx))
    )
    result = to_notes(answer.output, inputs.subtitles)
    write_model(layout.notes_cht_json, result)
    logger.success(f"Wrote {len(result.items)} notes: {layout.notes_cht_json}")
    return f"{len(result.items)} notes"


def _probe(ctx: StageContext) -> str:
    layout = ctx.layout
    inputs = _inputs(ctx)
    answer = ctx.agents.run(
        build_probe_task(
            inputs, session_dir=ctx.session_dir("probe"), tools=_tools(ctx)
        )
    )
    write_model(layout.notes_probe, answer.output)
    atomic_write_text(
        layout.notes_probe_report, render_probe(answer.output, inputs.subtitles)
    )
    noted = sum(candidate.annotate for candidate in answer.output.candidates)
    logger.success(
        f"Notes probe: {noted} of {len(answer.output.candidates)} candidates "
        f"would be noted: {layout.notes_probe_report}"
    )
    return f"{noted}/{len(answer.output.candidates)} would be noted"


def _outputs(layout: ProjectLayout) -> Sequence[Path]:
    return (layout.notes_cht_json,)


def _workdir(layout: ProjectLayout) -> Path:
    return layout.work_dir(StageKey.NOTES)


STAGE = StageDef(
    key=StageKey.NOTES,
    label="Translator's notes",
    weight=2,
    run=_run,
    outputs=_outputs,
    enabled=switch_enabled("notes"),
    applies=switch_applies("notes"),
    params=role_params(Role.EDITOR),
)
# `grill notes --probe`: what the agent would note and why, written beside
# the stage's work, never recorded in the ledger.
PROBE = DeliveryStepDef(
    key="notes_probe",
    label="Notes probe",
    weight=1,
    run=_probe,
    workdir=_workdir,
    params=role_params(Role.EDITOR),
)
