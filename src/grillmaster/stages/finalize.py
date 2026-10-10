"""Finalize stage: `work/11_glossary/checked.srt` into the deliverables
`subs/cht.ass` and `subs/cht.srt` (name spacing + Netflix-TC punctuation).

Name units come from the effective briefing and the fixed glossary. No
intermediates, so `work/12_finalize/` is never created.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.briefing import Briefing
from grillmaster.core.json_artifact import read_model
from grillmaster.core.srt import read_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.glossary.fixed import load_fixed_glossary
from grillmaster.pipeline.stage import StageDef, require
from grillmaster.subtitles.finalize import name_units, write_finalized

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from grillmaster.pipeline.stage import StageContext
    from grillmaster.project.layout import ProjectLayout


def _run(ctx: StageContext) -> None:
    layout = ctx.layout
    briefing = read_model(
        require(layout.effective_briefing(), StageKey.PREPASS), Briefing
    )
    blocks = write_finalized(
        read_srt_file(require(layout.glossary_checked_srt, StageKey.GLOSSARY)),
        units=name_units(briefing, load_fixed_glossary()),
        ass_path=layout.cht_ass,
        srt_path=layout.cht_srt,
    )
    logger.success(
        f"Finalized {len(blocks)} blocks: {layout.cht_ass}, {layout.cht_srt}"
    )


def _outputs(layout: ProjectLayout) -> Sequence[Path]:
    return (layout.cht_srt, layout.cht_ass)


STAGE = StageDef(
    key=StageKey.FINALIZE,
    label="Finalize subtitles",
    weight=1,
    run=_run,
    outputs=_outputs,
)
