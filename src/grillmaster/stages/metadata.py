"""Metadata stage: yt-dlp info (`work/01_metadata/info.json`), platform
extras and the broadcast date, recorded on `ProjectState`.

Extras and the date are best-effort (a missing date only leaves the
deliverable undated); the yt-dlp extraction itself must succeed. A re-run
never erases what an earlier run found: the date, the talents and the
on-air label are only overwritten by non-empty values (`grill reset`
clears them).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.stage_key import StageKey
from grillmaster.project.state import SourceInfo
from grillmaster.sources.ytdlp import fetch_info
from grillmaster.stages._common import source_request
from grillmaster.stages.base import StageDef

if TYPE_CHECKING:
    from grillmaster.config.model import AppConfig
    from grillmaster.project.state import ProjectState
    from grillmaster.stages.base import StageContext


def _run(ctx: StageContext) -> None:
    state = ctx.state
    request = source_request(ctx)
    platform = request.platform
    info = fetch_info(
        ctx.ytdlp, request.url, ctx.layout.metadata_info, options=request.options
    )
    extras = platform.fetch_extras(state.id, ctx.http)
    broadcast_date = platform.resolve_broadcast_date(info, extras)
    if broadcast_date is None:
        logger.warning(
            "Broadcast date could not be resolved; deliverables will use "
            "the undated name"
        )
    else:
        logger.info(f"Resolved broadcast date: {broadcast_date:%Y-%m-%d}")
    description = info.description if platform.describes_program else None

    def record(project: ProjectState) -> None:
        known = project.source
        project.name = info.filename
        if broadcast_date is not None:
            project.broadcast_date = broadcast_date
        project.source = SourceInfo(
            title=info.title or None,
            description=description or None,
            series=info.series,
            channel=info.channel,
            broadcast_label=extras.broadcast_label or known.broadcast_label,
            talents=list(extras.talents) or known.talents,
        )

    ctx.update(record)


def _params(config: AppConfig) -> dict[str, str]:
    return {"official_cc": "on" if config.features.official_subtitles else "off"}


def _clear_state(state: ProjectState) -> None:
    state.name = None
    state.broadcast_date = None
    state.source = SourceInfo()


STAGE = StageDef(
    key=StageKey.METADATA,
    label="Fetch metadata",
    weight=1,
    run=_run,
    params=_params,
    clear_state=_clear_state,
)
