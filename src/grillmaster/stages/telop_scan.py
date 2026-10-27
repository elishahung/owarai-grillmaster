"""Telop scan side task: once `video.mp4` exists, OCR its on-screen text
on the GPU into `.work/side/telop_scan/scan.json` (fixed-name cache; delete
it to re-OCR), beside the stages. The glossary check and proofread wait for
it and read the cards as a reference; the telop stage selects from it (and
runs the scan itself when this task did not finish)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.core.stage_key import SideTaskKey, StageKey
from grillmaster.stages import _telop
from grillmaster.stages.base import SideTaskDef

if TYPE_CHECKING:
    from grillmaster.config.model import AppConfig
    from grillmaster.stages.base import StageContext


def _run(ctx: StageContext) -> int:
    return len(_telop.scan(ctx).frames)


def _describe(frames: int) -> str:
    return f"{frames} frames"


def _params(_config: AppConfig) -> dict[str, str]:
    return {"ocr": "PP-OCRv5 server det + PP-OCRv6 medium ja rec"}


TASK: SideTaskDef[int] = SideTaskDef(
    key=SideTaskKey.TELOP_SCAN,
    label="Telop scan",
    weight=2,
    start_after=StageKey.COMBINE,
    run=_run,
    enabled=_telop.enabled,
    applies=_telop.applies,
    describe=_describe,
    params=_params,
)
