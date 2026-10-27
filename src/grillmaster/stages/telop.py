"""Telop stage, the last one: once the dialogue subtitles are final, build
the cards from the scan (waiting for the scan side task, or scanning now
when it did not finish), let the utility agent keep and translate the
worthwhile ones beside the final subtitles and the effective briefing, and
write `.subs/telop.cht.json`, which packaging burns in beside each card.

Runs when `--telop`, `[features] telop` or the program's `telop = true` asks
for it (`stages._telop`). `.work/15_telop/` keeps `cards.json` (the rules'
view, rewritten every run), the session and the fetched frames. The
deliverable is hand-editable; `grill reset <id> --only telop` selects again
from the kept scan. `grill telop` runs this stage on a finished or archived
project whatever its ledger says, after which `grill package` burns it in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.briefing import Briefing
from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.core.model_spec import Role
from grillmaster.core.srt import read_srt_file
from grillmaster.core.stage_key import SideTaskKey, StageKey
from grillmaster.core.tool_session import ToolSession
from grillmaster.stages import _telop
from grillmaster.stages._common import frames_tool, program_rules, role_params
from grillmaster.stages.base import StageDef, require
from grillmaster.subtitles.structure import check_aligned
from grillmaster.telop.errors import TelopError
from grillmaster.telop.ocr import require_packages
from grillmaster.telop.scan import build_cards
from grillmaster.telop.select import (
    SelectInputs,
    TelopSubtitles,
    build_select_task,
    to_subtitles,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from grillmaster.config.model import AppConfig
    from grillmaster.config.secrets import Secrets
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.stages.base import StageContext


def _run(ctx: StageContext) -> str:
    layout = ctx.layout
    transcript = read_srt_file(require(layout.ja_srt, StageKey.TRANSCRIPT))
    subtitles = read_srt_file(require(layout.cht_srt, StageKey.FINALIZE))
    if problems := check_aligned(transcript, subtitles):
        raise TelopError(
            f"{layout.cht_srt} does not pair with {layout.ja_srt} block by block: "
            f"{'; '.join(problems)}"
        )
    briefing = read_model(
        require(layout.effective_briefing(), StageKey.PREPASS), Briefing
    )
    ctx.side_tasks.wait(SideTaskKey.TELOP_SCAN)
    cards = build_cards(_telop.scan(ctx), transcript)
    write_model(layout.telop_cards, cards)
    offered = cards.offered
    logger.info(
        f"Telop pre-filter offers {len(offered)} of {len(cards.cards)} cards "
        f"for selection: {layout.telop_cards}"
    )
    result = TelopSubtitles()
    if offered:
        tools = ToolSession(
            project_root=layout.root,
            frames=frames_tool(ctx, layout.telop_frames_dir),
            check_srt=None,
            watch_clip=None,
        )
        inputs = SelectInputs(
            cards=offered,
            transcript=transcript,
            subtitles=subtitles,
            briefing=briefing,
            program_instruction=program_rules(
                ctx.config, ctx.state.source
            ).instruction_text(StageKey.TELOP),
        )
        answer = ctx.agents.run(
            build_select_task(inputs, session_dir=ctx.session_dir(), tools=tools)
        )
        result = to_subtitles(offered, answer.output)
    write_model(layout.telop_cht_json, result)
    logger.success(f"Selected {len(result.items)} telops: {layout.telop_cht_json}")
    return f"{len(result.items)} telops"


def _outputs(layout: ProjectLayout) -> Sequence[Path]:
    return (layout.telop_cht_json,)


def _preflight(_config: AppConfig, _secrets: Secrets) -> None:
    """The OCR extra, before any stage runs: the scan needs it, and missing
    it would otherwise fail this last stage after the whole translation."""
    require_packages()


STAGE = StageDef(
    key=StageKey.TELOP,
    label="Telop translation",
    weight=2,
    run=_run,
    outputs=_outputs,
    enabled=_telop.enabled,
    applies=_telop.applies,
    preflight=_preflight,
    params=role_params(Role.UTILITY),
)
