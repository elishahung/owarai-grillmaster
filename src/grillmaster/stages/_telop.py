"""Telop glue shared by the scan side task, the telop stage and the stages
that read the cards (glossary check, proofread, notes).

Whether a project gets telops is the most specific explicit choice:
`--telop` / `--no-telop`, then its program's `telop` (series over channel),
then `[features] telop`. `enabled` sees no project, so without a run flag it
stays on while the feature or any program may ask, and `applies` decides once
the program is known (`_common.switch_enabled` / `switch_applies`).
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
from grillmaster.stages._common import switch_applies, switch_enabled
from grillmaster.stages.base import require
from grillmaster.telop.ocr import SAMPLE_SIZE, gpu_engine, read_frames
from grillmaster.telop.reference import render_reference
from grillmaster.telop.scan import SAMPLE_FPS, TelopCards, TelopScan, build_cards

if TYPE_CHECKING:
    from grillmaster.stages.base import StageContext

PROGRESS_SCOPE = "telop"


enabled = switch_enabled("telop")
applies = switch_applies("telop")


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
    cards = kept_cards(ctx)
    if cards is None:
        logger.warning(f"No telop scan at {ctx.layout.telop_scan}; going without it")
        return None
    return render_reference(cards.offered)


def kept_cards(ctx: StageContext) -> TelopCards | None:
    """Every card of the kept scan, once the scan side task has finished;
    `None` without one (no OCR runs for a reader of the cards)."""
    ctx.side_tasks.wait(SideTaskKey.TELOP_SCAN)
    layout = ctx.layout
    found = load_model(layout.telop_scan, TelopScan)
    if found is None:
        return None
    transcript = read_srt_file(require(layout.ja_srt, StageKey.TRANSCRIPT))
    return build_cards(found, transcript)
