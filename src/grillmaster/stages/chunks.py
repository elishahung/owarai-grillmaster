"""Chunk stage: translate every chunk concurrently, then merge the results
onto the Japanese timecodes as `work/09_chunks/merged.srt`.

Each chunk works in its own `work/09_chunks/<from>-<to>/` (frames, audio
slice, session record, agent cwd, `translation.json` cache); the batch
itself is `translate.chunk.translate_chunks`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.briefing import Briefing
from grillmaster.core.json_artifact import read_model
from grillmaster.core.model_spec import Role
from grillmaster.core.srt import write_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.project.layout import session_dir
from grillmaster.stages._common import (
    accepts_audio,
    role_params,
    source_context,
    split_source,
)
from grillmaster.stages.base import StageDef, require
from grillmaster.translate.chunk import ChunkFiles, translate_chunks
from grillmaster.translate.inputs import ChunkBatchInputs

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.config.model import AppConfig
    from grillmaster.stages.base import StageContext


def _run(ctx: StageContext) -> None:
    layout = ctx.layout
    options = ctx.config.translate
    _blocks, chunks = split_source(ctx)
    briefing = read_model(
        require(layout.effective_briefing(), StageKey.PREPASS), Briefing
    )
    has_audio = accepts_audio(ctx.agents, Role.CHUNK)
    inputs = ChunkBatchInputs(
        source=source_context(ctx, StageKey.CHUNKS, with_parent=False),
        chunks=chunks,
        briefing=briefing,
        video=require(layout.video, StageKey.COMBINE),
        audio=require(layout.audio, StageKey.AUDIO) if has_audio else None,
        project_root=layout.root,
        frame_interval_s=options.chunk_frame_interval_s,
        frame_max_side=options.frame_max_side,
        attempts=options.chunk_attempts,
    )

    def chunk_session(from_index: int, to_index: int) -> Path:
        return session_dir(layout.chunk_dir(from_index, to_index))

    files = ChunkFiles(
        workdir=layout.chunk_dir,
        frames_dir=layout.chunk_frames_dir,
        audio=layout.chunk_audio,
        translation=layout.chunk_translation,
        session_dir=chunk_session,
    )
    merged = translate_chunks(inputs, files, ctx.agents, ctx.ffmpeg)
    write_srt_file(layout.merged_srt, merged)
    logger.success(f"Merged {len(merged)} translated blocks: {layout.merged_srt}")


def _char_limit(config: AppConfig) -> str:
    return str(config.translate.chunk_char_limit)


STAGE = StageDef(
    key=StageKey.CHUNKS,
    label="Translate chunks",
    weight=12,
    run=_run,
    params=role_params(Role.CHUNK, chunk_char_limit=_char_limit),
)
