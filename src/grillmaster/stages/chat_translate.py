"""Chat-translate stage (`--chat`): the replay in Traditional Chinese.

Runs last, after finalize: the finalized SRT (paired with `subs/ja.srt`) and
the effective briefing are the ground truth for names and context, and a
chat failure leaves the main subtitles finalized. Batches and the polish pass
cache under `work/13_chat_translate/` (fixed names); `subs/chat.cht.json` is
rebuilt from them on every run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.briefing import Briefing
from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.core.model_spec import Role
from grillmaster.core.srt import read_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.live_chat.schema import ChatLog
from grillmaster.live_chat.translate import (
    ChatTranslationFiles,
    ChatTranslationInputs,
    translate_live_chat,
)
from grillmaster.pipeline.stage import StageDef, require
from grillmaster.project.layout import session_dir

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.config.model import AppConfig
    from grillmaster.pipeline.stage import RunOptions, StageContext
    from grillmaster.project.layout import ProjectLayout


def _run(ctx: StageContext) -> None:
    layout = ctx.layout
    inputs = ChatTranslationInputs(
        log=read_model(require(layout.chat_messages, StageKey.CHAT_FETCH), ChatLog),
        briefing=read_model(
            require(layout.effective_briefing(), StageKey.PREPASS), Briefing
        ),
        source_subtitles=read_srt_file(require(layout.ja_srt, StageKey.TRANSCRIPT)),
        finalized_subtitles=read_srt_file(require(layout.cht_srt, StageKey.FINALIZE)),
    )
    sessions = ctx.workdir
    files = ChatTranslationFiles(
        batch_cache=layout.chat_batch,
        polish_cache=layout.chat_polish,
        session_dir=lambda label: session_dir(sessions, label=label),
    )
    translated = translate_live_chat(inputs, files, ctx.agents)
    write_model(layout.chat_cht_json, translated)
    logger.success(f"Translated chat saved: {layout.chat_cht_json}")


def _enabled(options: RunOptions) -> bool:
    return options.chat


def _outputs(layout: ProjectLayout) -> tuple[Path, ...]:
    return (layout.chat_cht_json,)


def _params(config: AppConfig) -> dict[str, str]:
    return {"model": str(config.agents.roles.spec(Role.CHAT))}


STAGE = StageDef(
    key=StageKey.CHAT_TRANSLATE,
    label="Translate live chat",
    weight=3,
    run=_run,
    outputs=_outputs,
    enabled=_enabled,
    params=_params,
)
