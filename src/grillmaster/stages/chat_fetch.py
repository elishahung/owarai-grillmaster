"""Chat-fetch stage (`--chat`): the live-chat replay, normalized onto `video.mp4`.

Runs right after combine, so a video without a replay fails before ASR
spends money, and so the section the video was cut to is already on
`ProjectState.section`. The raw replay (`work/04_chat_fetch/live_chat.jsonl`)
is a fixed-name cache; `messages.json` is rebuilt from it on every run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.core.json_artifact import write_model
from grillmaster.core.stage_key import StageKey
from grillmaster.live_chat.parse import parse_live_chat
from grillmaster.pipeline.stage import StageDef
from grillmaster.sources.live_chat import download_live_chat
from grillmaster.sources.registry import source_platform
from grillmaster.sources.ytdlp import YtDlpLibrary, shared_options

if TYPE_CHECKING:
    from grillmaster.config.model import AppConfig
    from grillmaster.pipeline.stage import RunOptions, StageContext
    from grillmaster.sources.ytdlp import YtDlp


def build(ytdlp: YtDlp) -> StageDef:
    """The stage downloading through `ytdlp` (tests pass a fake)."""

    def run(ctx: StageContext) -> None:
        state = ctx.state
        platform = source_platform(state.platform)
        download_live_chat(
            ytdlp,
            platform.url(state.id),
            ctx.layout.chat_raw,
            options=shared_options(platform, ctx.config.paths.cookies),
        )
        log = parse_live_chat(
            ctx.layout.chat_raw,
            section_start=state.section.start,
            section_end=state.section.end,
        )
        write_model(ctx.layout.chat_messages, log)

    def enabled(options: RunOptions) -> bool:
        return options.chat

    def params(_config: AppConfig) -> dict[str, str]:
        return {"tool": "yt-dlp"}

    return StageDef(
        key=StageKey.CHAT_FETCH,
        label="Fetch live chat",
        weight=1,
        run=run,
        outputs=lambda _layout: (),
        enabled=enabled,
        params=params,
    )


STAGE = build(YtDlpLibrary())
