"""Telop glue shared by the scan side task, the telop stage and the stages
that read the cards as a reference (glossary check, proofread).

Whether a project gets telops is the most specific explicit choice:
`--telop` / `--no-telop`, then its program's `telop` (series over channel),
then `[features] telop`. `enabled` sees no project, so without a run flag it
stays on while the feature or any program may ask, and `applies` decides once
the program is known.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.json_artifact import load_model, write_model
from grillmaster.core.srt import read_srt_file
from grillmaster.core.stage_key import SideTaskKey, StageKey
from grillmaster.events.progress import track
from grillmaster.media.frames import sample_frames
from grillmaster.stages._common import program_rules
from grillmaster.stages.base import require
from grillmaster.telop.ocr import SAMPLE_SIZE, gpu_engine, read_frames
from grillmaster.telop.reference import render_reference
from grillmaster.telop.scan import SAMPLE_FPS, TelopScan, build_cards

if TYPE_CHECKING:
    from grillmaster.config.model import AppConfig
    from grillmaster.stages.base import RunOptions, StageContext

PROGRESS_SCOPE = "telop"


def enabled(options: RunOptions, config: AppConfig) -> bool:
    """The run flag when given, else `[features] telop` or any program that
    switches it on."""
    if options.telop is not None:
        return options.telop
    return config.features.telop or any(
        rule.telop is True for _, _, rule in config.programs.rules()
    )


def applies(ctx: StageContext) -> bool:
    """For this project: the run flag, else its program's rule, else the
    feature."""
    if ctx.options.telop is not None:
        return ctx.options.telop
    rule = program_rules(ctx.config, ctx.state.source).telop
    return ctx.config.features.telop if rule is None else rule


def scan(ctx: StageContext) -> TelopScan:
    """The OCR of every sampled frame: `scan.json` when it exists, else
    sampled and read now (and written there)."""
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
            result = read_frames(engine, frames, advance=advance)
    write_model(layout.telop_scan, result)
    return result


def reference(ctx: StageContext) -> str | None:
    """The on-screen text table for a pass over the dialogue subtitles, once
    the scan side task has finished; `None` when the project has no telops
    or no scan (a failed scan is the telop stage's to retry)."""
    if not applies(ctx):
        return None
    ctx.side_tasks.wait(SideTaskKey.TELOP_SCAN)
    layout = ctx.layout
    found = load_model(layout.telop_scan, TelopScan)
    if found is None:
        logger.warning(f"No telop scan at {layout.telop_scan}; going without it")
        return None
    transcript = read_srt_file(require(layout.ja_srt, StageKey.TRANSCRIPT))
    return render_reference(build_cards(found, transcript).offered)
