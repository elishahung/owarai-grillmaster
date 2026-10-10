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
from grillmaster.sources.live_chat import download_live_chat
from grillmaster.stages._common import chat_enabled, source_request, tool_params
from grillmaster.stages.base import StageDef

if TYPE_CHECKING:
    from grillmaster.stages.base import StageContext


def _run(ctx: StageContext) -> None:
    request = source_request(ctx)
    download_live_chat(
        ctx.ytdlp, request.url, ctx.layout.chat_raw, options=request.options
    )
    section = ctx.state.section
    log = parse_live_chat(
        ctx.layout.chat_raw, section_start=section.start, section_end=section.end
    )
    write_model(ctx.layout.chat_messages, log)


STAGE = StageDef(
    key=StageKey.CHAT_FETCH,
    label="Fetch live chat",
    weight=1,
    run=_run,
    enabled=chat_enabled,
    params=tool_params("yt-dlp"),
)
