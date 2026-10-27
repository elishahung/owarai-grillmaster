"""Telop side task: once the pre-pass has its briefing, OCR the video's
on-screen text cards on the GPU, pre-filter them by rules and let the
utility agent keep and translate the worthwhile ones into
`.subs/telop.cht.json`, which packaging burns in at the top.

Runs beside the translation stages when `--telop`, `[features] telop` or
the program's `telop = true` asks for it (the program is known only at
the start point, hence `applies`; `enabled` stays on while any program may
ask). `grill telop` runs the same task on a finished or archived project,
after which `grill package` burns it in.

Caches hit on fixed names: `scan.json` (the OCR, the slow part) and the
deliverable itself; delete `.subs/telop.cht.json` to select again from the
kept scan, or edit it by hand and re-package.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.briefing import Briefing
from grillmaster.core.json_artifact import load_model, read_model, write_model
from grillmaster.core.model_spec import Role
from grillmaster.core.srt import read_srt_file
from grillmaster.core.stage_key import SideTaskKey, StageKey
from grillmaster.core.tool_session import ToolSession
from grillmaster.events.progress import track
from grillmaster.media.frames import sample_frames
from grillmaster.stages._common import (
    flag_or_feature,
    frames_tool,
    program_rules,
    role_params,
)
from grillmaster.stages.base import SideTaskDef, require
from grillmaster.telop.ocr import SAMPLE_SIZE, gpu_engine, read_frames
from grillmaster.telop.scan import SAMPLE_FPS, CardKind, TelopScan, build_cards
from grillmaster.telop.select import (
    SelectInputs,
    TelopSubtitles,
    build_select_task,
    to_subtitles,
)

if TYPE_CHECKING:
    from grillmaster.config.model import AppConfig
    from grillmaster.stages.base import RunOptions, StageContext

PROGRESS_SCOPE = "telop"
_asked = flag_or_feature("telop")


def _run(ctx: StageContext) -> int:
    layout = ctx.layout
    output = layout.telop_cht_json
    cached = load_model(output, TelopSubtitles)
    if cached is not None:
        logger.info(f"Telops already selected, skipping: {output}")
        return len(cached.items)
    transcript = read_srt_file(require(layout.ja_srt, StageKey.TRANSCRIPT))
    briefing = read_model(
        require(layout.effective_briefing(), StageKey.PREPASS), Briefing
    )
    cards = build_cards(_scan(ctx), transcript)
    write_model(layout.telop_cards, cards)
    candidates = [card for card in cards.cards if card.kind is CardKind.CANDIDATE]
    logger.info(
        f"Telop pre-filter kept {len(candidates)} of {len(cards.cards)} cards "
        f"for selection: {layout.telop_cards}"
    )
    subtitles = TelopSubtitles()
    if candidates:
        tools = ToolSession(
            project_root=layout.root,
            frames=frames_tool(ctx, layout.telop_frames_dir),
            check_srt=None,
            watch_clip=None,
        )
        inputs = SelectInputs(
            cards=candidates,
            transcript=transcript,
            briefing=briefing,
            program_instruction=program_rules(
                ctx.config, ctx.state.source
            ).instruction_text(SideTaskKey.TELOP),
        )
        result = ctx.agents.run(
            build_select_task(inputs, session_dir=ctx.session_dir(), tools=tools)
        )
        subtitles = to_subtitles(candidates, result.output)
    write_model(output, subtitles)
    logger.success(f"Selected {len(subtitles.items)} telops: {output}")
    return len(subtitles.items)


def _scan(ctx: StageContext) -> TelopScan:
    """The OCR of every sampled frame, from `scan.json` when it exists."""
    layout = ctx.layout
    cached = load_model(layout.telop_scan, TelopScan)
    if cached is not None:
        logger.info(f"Telop scan exists, skipping the OCR: {layout.telop_scan}")
        return cached
    # The engine first: a missing extra or GPU fails before the sampling.
    engine = gpu_engine()
    video = require(layout.video, StageKey.COMBINE)
    # A local scratch directory: about a gigabyte of stills, read once, and
    # the project may sit on a network share (`grill telop` on an archive).
    with tempfile.TemporaryDirectory(
        prefix="grill-telop-", ignore_cleanup_errors=True
    ) as samples:
        frames = sample_frames(
            ctx.ffmpeg, video, Path(samples), fps=SAMPLE_FPS, size=SAMPLE_SIZE
        )
        with track(ctx.events, PROGRESS_SCOPE, "Telop OCR", len(frames)) as advance:
            scan = read_frames(engine, frames, advance=advance)
    write_model(layout.telop_scan, scan)
    return scan


def _describe(count: int) -> str:
    return f"{count} telops"


def _enabled(options: RunOptions, config: AppConfig) -> bool:
    """`--telop`, `[features] telop`, or any program that may switch it on."""
    return _asked(options, config) or any(
        rule.telop for _, _, rule in config.programs.rules()
    )


def _applies(ctx: StageContext) -> bool:
    """For this project: the run flag, the feature, or its program's rule."""
    return (
        _asked(ctx.options, ctx.config)
        or program_rules(ctx.config, ctx.state.source).telop
    )


TASK: SideTaskDef[int] = SideTaskDef(
    key=SideTaskKey.TELOP,
    label="Telop translation",
    weight=2,
    start_after=StageKey.PREPASS,
    run=_run,
    enabled=_enabled,
    applies=_applies,
    describe=_describe,
    params=role_params(
        Role.UTILITY, ocr="PP-OCRv5 server det + PP-OCRv6 medium ja rec"
    ),
)
