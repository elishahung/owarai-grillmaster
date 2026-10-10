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
from grillmaster.pipeline.stage import StageDef
from grillmaster.project.state import SourceInfo, Talent
from grillmaster.sources.http import UrllibJsonHttp
from grillmaster.sources.registry import source_platform
from grillmaster.sources.ytdlp import YtDlpLibrary, fetch_info, shared_options

if TYPE_CHECKING:
    from grillmaster.config.model import AppConfig
    from grillmaster.pipeline.stage import StageContext
    from grillmaster.project.state import ProjectState
    from grillmaster.sources.http import JsonHttp
    from grillmaster.sources.ytdlp import YtDlp


def build(ytdlp: YtDlp, http: JsonHttp) -> StageDef:
    """The stage reaching yt-dlp and the platform APIs through the seams."""

    def run(ctx: StageContext) -> None:
        state = ctx.state
        platform = source_platform(state.platform)
        info = fetch_info(
            ytdlp,
            platform.url(state.id),
            ctx.layout.metadata_info,
            options=shared_options(platform, ctx.config.paths.cookies),
        )
        extras = platform.fetch_extras(state.id, http)
        broadcast_date = platform.resolve_broadcast_date(info, extras)
        if broadcast_date is None:
            logger.warning(
                "Broadcast date could not be resolved; deliverables will use "
                "the undated name"
            )
        else:
            logger.info(f"Resolved broadcast date: {broadcast_date:%Y-%m-%d}")
        description = info.description if platform.describes_program else None
        talents = [
            Talent(
                id=talent.id,
                name=talent.name,
                name_kana=talent.name_kana,
                roles=list(talent.roles),
            )
            for talent in extras.talents
        ]

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
                talents=talents or known.talents,
            )

        ctx.update(record)

    def params(config: AppConfig) -> dict[str, str]:
        return {"official_cc": "on" if config.features.official_subtitles else "off"}

    return StageDef(
        key=StageKey.METADATA,
        label="Fetch metadata",
        weight=1,
        run=run,
        outputs=lambda _layout: (),
        params=params,
        clear_state=_clear_state,
    )


def _clear_state(state: ProjectState) -> None:
    state.name = None
    state.broadcast_date = None
    state.source = SourceInfo()


STAGE = build(YtDlpLibrary(), UrllibJsonHttp())
