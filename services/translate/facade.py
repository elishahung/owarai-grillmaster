"""Translation orchestrator: pre-pass + concurrent chunked translation.

Flow: parse SRT → split into N char-balanced chunks → `run_pre_pass` makes one
whole-film analysis call → `translate_chunks` translates chunks concurrently
(bounded by a semaphore) → normalize merged indices → write output. Each stage
picks its backend via `settings.agent_prepass_model` /
`settings.agent_chunk_model`.
"""

import asyncio
import time

from loguru import logger

from settings import settings
from services.srt import SrtBlock, parse_srt, serialize_srt
from services.progress import NoopProgressReporter
from services.inference import Backend, backend_supports_audio
from .assets import prepare_chunk_media_assets
from .chunk.chunk_worker import translate_chunk
from .chunker import split_into_chunks
from .errors import ChunkTranslationError, TranslationError
from .chunk.normalizer import normalize_translated_blocks
from .pre_pass.pre_pass import PrePassResult, run_pre_pass as execute_pre_pass
from .request import TranslationRequest


def _prepare(request: TranslationRequest) -> tuple[str, list[list[SrtBlock]]]:
    """Parse the source SRT and split it into deterministic chunks.

    Side-effect free and identical across both stages so the pre-pass and
    chunk-translation stages always agree on chunk boundaries.
    """
    srt_text = request.srt_path.read_text(encoding="utf-8")
    blocks = parse_srt(srt_text)
    logger.info(f"Parsed {len(blocks)} SRT blocks")

    chunks = split_into_chunks(blocks, settings.chunk_char_limit)
    total_chars = sum(b.char_count for b in blocks)
    logger.info(
        f"Split into {len(chunks)} chunks "
        f"(total {total_chars} chars, avg {total_chars // max(1, len(chunks))} chars/chunk, "
        f"chunk_char_limit={settings.chunk_char_limit})"
    )
    for i, c in enumerate(chunks):
        logger.debug(
            f"  chunk {i + 1}/{len(chunks)}: index {c[0].index}–{c[-1].index} "
            f"({len(c)} blocks, {sum(b.char_count for b in c)} chars)"
        )
    return srt_text, chunks


def _read_official_subtitle_text(request: TranslationRequest) -> str | None:
    """Read the platform CC reference, if the download produced one."""
    if (
        request.official_subtitle_path is None
        or not request.official_subtitle_path.exists()
    ):
        return None
    return request.official_subtitle_path.read_text(encoding="utf-8")


def run_pre_pass(request: TranslationRequest) -> None:
    """Run the pre-pass only and persist pre_pass.json.

    Blocks until complete. The persisted briefing is the explicit hand-off
    consumed by `translate_chunks`; this stage does no chunk translation.
    """
    start_time = time.time()
    logger.info(f"Starting pre-pass for SRT file: {request.srt_path}")
    srt_text, chunks = _prepare(request)
    execute_pre_pass(
        srt_text,
        request.video_path,
        request.audio_path,
        chunks,
        request.pre_pass_path,
        request.pre_pass_cache_dir,
        video_title=request.video_title,
        video_description=request.video_description,
        translation_hint=request.translation_hint,
        source_metadata_context=request.source_metadata_context,
        parent_pre_pass_context=request.parent_pre_pass_context,
        official_subtitle_context=_read_official_subtitle_text(request),
        program_instruction=request.program_instruction,
    )
    logger.success(f"Pre-pass done: {time.time() - start_time:.1f}s")


def translate_chunks(
    request: TranslationRequest,
    progress: NoopProgressReporter | None = None,
) -> None:
    """Translate all chunks concurrently using the persisted pre-pass.

    Blocks until complete. Requires `run_pre_pass` to have already written
    pre_pass.json; this stage never re-runs the pre-pass. Raises
    `TranslationError` when any chunk fails (after every chunk task finished,
    so successful chunks keep their caches).
    """
    asyncio.run(_translate_chunks_async(request, progress))


async def _translate_chunks_async(
    request: TranslationRequest,
    progress: NoopProgressReporter | None,
) -> None:
    start_time = time.time()
    logger.info(f"Starting chunk translation for SRT file: {request.srt_path}")
    _srt_text, chunks = _prepare(request)

    if not request.pre_pass_path.exists():
        raise TranslationError(
            f"pre_pass.json not found at {request.pre_pass_path}; "
            "run the pre-pass stage first"
        )
    pre_pass_result = PrePassResult.model_validate_json(
        request.pre_pass_path.read_text(encoding="utf-8")
    )

    official_subtitle_text = _read_official_subtitle_text(request)
    official_subtitle_blocks = None
    if official_subtitle_text:
        # The reference is best-effort: a corrupt file must not fail the
        # translation stage.
        try:
            official_subtitle_blocks = parse_srt(official_subtitle_text)
        except ValueError as e:
            logger.warning(
                f"Official subtitle reference unparsable; ignoring: {e}"
            )
    if official_subtitle_blocks:
        logger.info(
            f"Official subtitle reference loaded: "
            f"{len(official_subtitle_blocks)} blocks"
        )

    request.chunks_cache_dir.mkdir(parents=True, exist_ok=True)
    has_audio = backend_supports_audio(
        Backend(settings.agent_chunk_model.backend)
    )
    semaphore = asyncio.Semaphore(settings.agent_concurrency)

    async def bounded(i: int, chunk: list[SrtBlock]):
        async with semaphore:
            from_index = chunk[0].index
            to_index = chunk[-1].index
            if progress is not None:
                progress.chunk_started(i, len(chunks), from_index, to_index)
            try:
                # ffmpeg extraction is blocking; keep the event loop free so
                # the other chunks in this wave start preparing concurrently.
                chunk_assets = await asyncio.to_thread(
                    prepare_chunk_media_assets,
                    video_path=request.video_path,
                    audio_path=request.audio_path,
                    cache_root=request.chunks_cache_dir,
                    chunk=chunk,
                    chunk_index=i,
                    total_chunks=len(chunks),
                    interval_seconds=settings.chunk_frame_interval_seconds,
                    max_side=settings.video_frame_max_side,
                    extract_audio=has_audio,
                )
                result = await translate_chunk(
                    chunk_assets,
                    chunk,
                    i,
                    len(chunks),
                    pre_pass_result,
                    official_subtitle_blocks=official_subtitle_blocks,
                    program_instruction=request.program_instruction,
                )
            except Exception as e:
                if progress is not None:
                    retries = (
                        e.retries if isinstance(e, ChunkTranslationError) else 0
                    )
                    progress.chunk_failed(i, str(e), retries=retries)
                raise
            if progress is not None:
                progress.chunk_finished(i, result.retries)
            return result

    async def observed(i: int, chunk: list[SrtBlock]):
        try:
            return i, await bounded(i, chunk)
        except Exception as e:
            return i, e

    raw_chunk_results: list[object] = [None] * len(chunks)
    chunk_tasks = [observed(i, c) for i, c in enumerate(chunks)]
    for completed in asyncio.as_completed(chunk_tasks):
        i, result = await completed
        raw_chunk_results[i] = result

    chunk_results = []
    total_retries = 0
    chunk_failures: list[str] = []
    for i, (chunk, result) in enumerate(zip(chunks, raw_chunk_results)):
        if isinstance(result, ChunkTranslationError):
            total_retries += result.retries
            chunk_failures.append(f"{result.chunk_label}: {result}")
        elif isinstance(result, Exception):
            chunk_failures.append(
                f"[chunk {i + 1}/{len(chunks)}] "
                f"index {chunk[0].index}–{chunk[-1].index}: {result}"
            )
        else:
            total_retries += result.retries
            chunk_results.append(result)

    if chunk_failures:
        for failure in chunk_failures:
            logger.error(f"{failure} (failed after all tasks completed)")
        raise TranslationError(
            f"{len(chunk_failures)}/{len(chunks)} chunks failed "
            f"({len(chunk_results)} completed, retries={total_retries}): "
            + "; ".join(chunk_failures)
        )

    # Merge chunk outputs, then rebuild contiguous SRT indices because
    # chunk validation may tolerate a small number of dropped blocks.
    all_blocks: list[SrtBlock] = []
    for r in chunk_results:
        all_blocks.extend(r.blocks)
    all_blocks = normalize_translated_blocks(all_blocks)
    all_blocks = [
        SrtBlock(index=i, timecode=block.timecode, text=block.text)
        for i, block in enumerate(all_blocks, start=1)
    ]

    request.output_path.parent.mkdir(parents=True, exist_ok=True)
    request.output_path.write_text(serialize_srt(all_blocks), encoding="utf-8")
    logger.success(f"Translation saved to: {request.output_path}")

    elapsed = time.time() - start_time
    logger.info(
        f"Chunk translation done: {len(chunks)} chunks, {total_retries} retries, "
        f"{elapsed:.1f}s ({elapsed / 60:.2f} min, "
        f"agent_concurrency={settings.agent_concurrency})"
    )
