"""One chunk's translation: the agent task, its output schema and validator,
and the SRT rebuilt from it.

The agent returns `{blocks: [{index, text}]}` through the native schema
channel and never writes timecodes; Python puts each text back under its
source block's index and timecode. Blank lines and lines holding only a
speaker dash (`-`) are debris (the first would break the SRT, the second is
what models leave after a two-speaker line): rebuilding drops them, and the
validator judges a block's text as it will be emitted, so a block that is
nothing but debris counts as empty and goes back for repair.

`translate_chunks` runs a whole batch: each chunk's `translation.json` is
its fixed-name cache, written the moment its session is accepted.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.agents.errors import ValidationFailure
from grillmaster.agents.task import AgentJob, AgentTask, SchemaOutput
from grillmaster.core.id_coverage import id_coverage
from grillmaster.core.json_artifact import load_model, write_model
from grillmaster.core.model_spec import Role
from grillmaster.core.models import StrictModel
from grillmaster.core.srt import chunk_range_name, reindex
from grillmaster.core.tool_session import FramesTool, ToolSession
from grillmaster.translate import prompt
from grillmaster.translate.assets import prepare_chunk_assets
from grillmaster.translate.errors import TranslateError
from grillmaster.translate.inputs import ChunkInputs

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from pathlib import Path

    from grillmaster.agents.runner import AgentRunner
    from grillmaster.agents.task import AgentResult
    from grillmaster.core.srt import SrtBlock
    from grillmaster.media.ffmpeg import FfmpegRunner
    from grillmaster.translate.chunker import Chunk
    from grillmaster.translate.inputs import ChunkBatchInputs

    # A per-chunk path, by the chunk's inclusive SRT index range.
    type ChunkPath = Callable[[int, int], Path]

_DEBRIS_LINES = frozenset({"", "-"})


class ChunkLine(StrictModel):
    """The translated text for source block `index`."""

    index: int
    text: str


class ChunkTranslation(StrictModel):
    blocks: list[ChunkLine]


@dataclass(frozen=True, slots=True)
class ChunkFiles:
    """Where each chunk keeps its files, by its inclusive index range.

    `workdir` is the agent's cwd (one per chunk: concurrent sessions may not
    share one); `frames_dir` and `audio` are its media caches;
    `translation` its fixed-name cache; `session_dir` its session records.
    """

    workdir: ChunkPath
    frames_dir: ChunkPath
    audio: ChunkPath
    translation: ChunkPath
    session_dir: ChunkPath


def translate_chunks(
    inputs: ChunkBatchInputs,
    files: ChunkFiles,
    agents: AgentRunner,
    ffmpeg: FfmpegRunner,
    *,
    on_chunk_done: Callable[[], None] = lambda: None,
) -> list[SrtBlock]:
    """Every chunk translated and merged onto the source timecodes.

    A cached translation skips its chunk's media and agent; one that no
    longer fits the source fails before any work. Each pending chunk
    prepares its media on a worker, and its accepted translation is cached
    at once, so a failure, crash or Ctrl-C elsewhere never loses it. Raises
    `TranslateError` listing the failed chunks after the whole batch ran.

    `on_chunk_done` is called once per chunk whose translation is in hand:
    for a cache hit while scanning, for an agent result right after it is
    cached (on that worker's thread).
    """
    chunks = inputs.chunks
    translations: dict[tuple[int, int], ChunkTranslation] = {}
    pending: list[Chunk] = []
    for chunk in chunks:
        cached = _cached_translation(files.translation(*chunk.index_range), chunk)
        if cached is None:
            pending.append(chunk)
        else:
            translations[chunk.index_range] = cached
            on_chunk_done()
    logger.info(
        f"Translating {len(pending)}/{len(chunks)} chunks "
        f"({len(translations)} cached, audio "
        f"{'on' if inputs.audio is not None else 'off'})"
    )

    def job(position: int, chunk: Chunk) -> AgentJob[ChunkTranslation]:
        def accept(result: AgentResult[ChunkTranslation]) -> None:
            write_model(files.translation(*chunk.index_range), result.output)
            translations[chunk.index_range] = result.output
            on_chunk_done()

        return AgentJob(
            task_name(chunk),
            prepare=lambda: _chunk_task(inputs, files, ffmpeg, position),
            accept=accept,
        )

    failures = agents.run_jobs([job(chunks.index(chunk), chunk) for chunk in pending])
    if failures:
        raise TranslateError(
            f"{len(failures)}/{len(chunks)} chunks failed "
            f"({len(chunks) - len(failures)} done):\n"
            + "\n".join(str(failure) for failure in failures)
        )
    return merge_chunks(
        rebuild_blocks(chunk, translations[chunk.index_range]) for chunk in chunks
    )


def _cached_translation(path: Path, chunk: Chunk) -> ChunkTranslation | None:
    """The chunk's stored translation; one that no longer fits the source
    fails loudly instead of being merged or silently redone."""
    cached = load_model(path, ChunkTranslation)
    if cached is None:
        return None
    problems = translation_problems(chunk, cached)
    if problems:
        raise TranslateError(
            f"{path} does not match {task_name(chunk)} of the current source "
            f"({'; '.join(problems)}); delete it or run "
            "`grill reset <id> --only chunks`"
        )
    return cached


def _chunk_task(
    inputs: ChunkBatchInputs, files: ChunkFiles, ffmpeg: FfmpegRunner, position: int
) -> AgentTask[ChunkTranslation]:
    """Cut chunk `position`'s media and build its call, `get_frames` scoped
    to its time range."""
    chunk = inputs.chunks[position]
    span = chunk.time_range
    frames_dir = files.frames_dir(*chunk.index_range)
    assets = prepare_chunk_assets(
        ffmpeg,
        video=inputs.video,
        chunk=chunk,
        frames_dir=frames_dir,
        interval_s=inputs.frame_interval_s,
        max_side=inputs.frame_max_side,
        audio_source=inputs.audio,
        audio_out=files.audio(*chunk.index_range),
    )
    tools = ToolSession(
        project_root=inputs.project_root,
        frames=FramesTool(
            video=inputs.video,
            frames_dir=frames_dir,
            window=(span.start, span.end),
            max_side=inputs.frame_max_side,
        ),
        check_srt=None,
    )
    return build_chunk_task(
        ChunkInputs(
            source=inputs.source,
            chunk=chunk,
            position=position,
            total=len(inputs.chunks),
            briefing=inputs.briefing,
            assets=assets,
        ),
        session_dir=files.session_dir(*chunk.index_range),
        workdir=files.workdir(*chunk.index_range),
        tools=tools,
        attempts=inputs.attempts,
    )


def task_name(chunk: Chunk) -> str:
    """`chunks/0001-0119`: events and the TUI chunk board key on this prefix."""
    return f"chunks/{chunk_range_name(chunk.from_index, chunk.to_index)}"


def build_chunk_task(
    inputs: ChunkInputs,
    *,
    session_dir: Path,
    workdir: Path,
    tools: ToolSession,
    attempts: int,
) -> AgentTask[ChunkTranslation]:
    """The agent call for one chunk: frames, optional audio slice, the
    `get_frames` tool scoped by the caller, and the coverage validator."""
    assets = inputs.assets
    return AgentTask(
        name=task_name(inputs.chunk),
        role=Role.CHUNK,
        instructions=prompt.chunk_instruction(
            inputs.source.program_instruction, has_audio=assets.audio is not None
        ),
        prompt=prompt.chunk_message(inputs),
        session_dir=session_dir,
        workdir=workdir,
        output=SchemaOutput(ChunkTranslation),
        images=tuple(frame.path for frame in assets.frames),
        audio=(assets.audio,) if assets.audio is not None else (),
        tools=tools,
        validate=chunk_validator(inputs.chunk),
        attempts=attempts,
    )


def chunk_validator(chunk: Chunk) -> Callable[[ChunkTranslation], None]:
    """Reject a translation that does not cover `chunk` exactly once per index
    with non-empty text; the message lists every offending index."""

    def validate(translation: ChunkTranslation) -> None:
        problems = translation_problems(chunk, translation)
        if problems:
            raise ValidationFailure.from_problems(
                f"翻譯結果必須對 index {chunk.from_index}-{chunk.to_index} 的每個來源"
                "區塊各輸出恰好一筆非空的 text。請修正下列問題後重新輸出完整的 blocks：",
                problems,
            )

    return validate


def translation_problems(chunk: Chunk, translation: ChunkTranslation) -> list[str]:
    """Missing, duplicate and unexpected indexes, and empty texts (judged
    after `clean_text`); empty = valid."""
    coverage = id_coverage(
        (block.index for block in chunk.blocks),
        ((line.index, clean_text(line.text)) for line in translation.blocks),
    )
    return [
        f"{label}：{', '.join(map(str, indexes))}"
        for label, indexes in (
            ("缺少 index", coverage.missing),
            ("重複的 index", coverage.duplicate),
            ("不屬於本區段的 index", coverage.unknown),
            ("text 為空的 index", coverage.empty),
        )
        if indexes
    ]


def rebuild_blocks(chunk: Chunk, translation: ChunkTranslation) -> list[SrtBlock]:
    """The chunk's source blocks carrying the translated (cleaned) text.

    `translation` must already have passed `translation_problems` (as the
    task validator or the cache check); it is not checked again here.
    """
    texts = {line.index: clean_text(line.text) for line in translation.blocks}
    return [replace(block, text=texts[block.index]) for block in chunk.blocks]


def merge_chunks(chunks: Iterable[Sequence[SrtBlock]]) -> list[SrtBlock]:
    """Rebuilt chunks concatenated in order and renumbered from 1."""
    return reindex(block for chunk in chunks for block in chunk)


def clean_text(text: str) -> str:
    """`text` without blank lines (an SRT block cannot hold one) and without
    lines that hold nothing but a speaker dash."""
    return "\n".join(
        line for line in text.splitlines() if line.strip() not in _DEBRIS_LINES
    )
