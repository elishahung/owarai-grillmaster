"""Finalize stage: `work/11_glossary/checked.srt` into the deliverables
`video.cht.ass` and `video.cht.srt` beside `video.mp4`, where players pick
them up (name spacing + Netflix-TC punctuation).

Name units come from the effective briefing and the fixed glossary. No
intermediates, so `work/12_finalize/` is never created. The step result
lists the written files, project-relative.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.briefing import Briefing
from grillmaster.core.json_artifact import read_model
from grillmaster.core.srt import read_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.glossary.fixed import load_fixed_glossary
from grillmaster.stages.base import StageDef, require
from grillmaster.subtitles.finalize import name_units, write_finalized

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from grillmaster.project.layout import ProjectLayout
    from grillmaster.stages.base import StageContext


def _run(ctx: StageContext) -> str:
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
    return ", ".join(
        path.relative_to(layout.root).as_posix()
        for path in (layout.cht_ass, layout.cht_srt)
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
