"""Gemini translation orchestrator: pre-pass + concurrent chunked translation."""

import asyncio
import time
from pathlib import Path

from google import genai
from loguru import logger
from pydantic import BaseModel

from settings import settings
from services.srt import SrtBlock, parse_srt, serialize_srt
from .assets import prepare_chunk_media_assets
from .chunk_worker import translate_chunk
from .chunker import split_into_chunks
from .errors import (
    ChunkTranslationError,
    GeminiTranslationError,
    PrePassError,
    TranslationCostSummary,
)
from .normalizer import normalize_translated_blocks
from .pre_pass import PrePassResult, run_pre_pass as execute_pre_pass


class TranslationResult(TranslationCostSummary):
    pass


class TranslationRequest(BaseModel):
    """Inputs required to run the Gemini translation pipeline."""

    video_description: str | None
    srt_path: Path
    audio_key: str
    video_path: Path
    audio_path: Path
    output_path: Path
    pre_pass_path: Path
    pre_pass_cache_dir: Path
    chunks_cache_dir: Path
    source_metadata_context: str | None = None
    parent_pre_pass_context: str | None = None


class Gemini:
    """Google Gemini client for SRT subtitle translation.

    Flow: parse SRT → split into N char-balanced chunks → run one pre-pass
    analysis call → translate chunks concurrently (bounded by a semaphore) →
    normalize merged indices → write output.
    """

    def __init__(self):
        logger.debug("Initializing Gemini client")
        self.client = genai.Client(api_key=settings.gemini_api_key)
        logger.info(
            f"Gemini client initialized "
            f"(concurrency={settings.gemini_concurrency}, "
            f"chunk_char_limit={settings.gemini_chunk_char_limit})"
        )

    def _prepare(
        self, request: TranslationRequest
    ) -> tuple[str, list[list[SrtBlock]]]:
        """Parse the source SRT and split it into deterministic chunks.

        Side-effect free and identical across both stages so the pre-pass and
        chunk-translation stages always agree on chunk boundaries.
        """
        srt_text = request.srt_path.read_text(encoding="utf-8")
        blocks = parse_srt(srt_text)
        logger.info(f"Parsed {len(blocks)} SRT blocks")

        chunks = split_into_chunks(blocks, settings.gemini_chunk_char_limit)
        total_chars = sum(b.char_count for b in blocks)
        logger.info(
            f"Split into {len(chunks)} chunks "
            f"(total {total_chars} chars, avg {total_chars // max(1, len(chunks))} chars/chunk)"
        )
        for i, c in enumerate(chunks):
            logger.debug(
                f"  chunk {i + 1}/{len(chunks)}: index {c[0].index}–{c[-1].index} "
                f"({len(c)} blocks, {sum(b.char_count for b in c)} chars)"
            )
        return srt_text, chunks

    def run_pre_pass(self, request: TranslationRequest) -> TranslationResult:
        """Run the Gemini pre-pass only and persist pre_pass.json.

        Blocks until complete. The persisted briefing is the explicit hand-off
        consumed by `translate_chunks`; this stage does no chunk translation.
        """
        return asyncio.run(self._run_pre_pass_async(request))

    async def _run_pre_pass_async(
        self, request: TranslationRequest
    ) -> TranslationResult:
        start_time = time.time()
        logger.info(f"Starting pre-pass for SRT file: {request.srt_path}")
        srt_text, chunks = self._prepare(request)

        try:
            _result, pre_pass_cost = await execute_pre_pass(
                self.client,
                request.video_description,
                srt_text,
                request.video_path,
                request.audio_path,
                chunks,
                request.pre_pass_path,
                request.pre_pass_cache_dir,
                request.source_metadata_context,
                request.parent_pre_pass_context,
            )
        except PrePassError as e:
            summary = TranslationResult(
                total_cost=e.accumulated_cost,
                pre_pass_cost=e.accumulated_cost,
                chunk_costs=[],
                num_chunks=len(chunks),
                retries=0,
                elapsed_seconds=time.time() - start_time,
                completed_chunks=0,
                failed_chunks=["pre-pass"],
            )
            logger.error(
                f"Pre-pass failed: ${summary.total_cost:.4f} spent after "
                f"{summary.elapsed_seconds:.1f}s"
            )
            raise GeminiTranslationError(str(e), summary) from e

        summary = TranslationResult(
            total_cost=pre_pass_cost,
            pre_pass_cost=pre_pass_cost,
            chunk_costs=[],
            num_chunks=len(chunks),
            retries=0,
            elapsed_seconds=time.time() - start_time,
            completed_chunks=0,
            failed_chunks=[],
        )
        logger.success(
            f"Pre-pass done: ${summary.total_cost:.4f}, "
            f"{summary.elapsed_seconds:.1f}s"
        )
        return summary

    def translate_chunks(
        self, request: TranslationRequest
    ) -> TranslationResult:
        """Translate all chunks concurrently using the persisted pre-pass.

        Blocks until complete. Requires `run_pre_pass` to have already written
        pre_pass.json; this stage never re-runs the pre-pass.
        """
        return asyncio.run(self._translate_chunks_async(request))

    async def _translate_chunks_async(
        self, request: TranslationRequest
    ) -> TranslationResult:
        def build_summary(
            *,
            chunk_costs: list[float],
            retries: int,
            completed_chunks: int,
            failed_chunks: list[str],
            num_chunks: int,
        ) -> TranslationResult:
            return TranslationResult(
                total_cost=sum(chunk_costs),
                pre_pass_cost=0.0,
                chunk_costs=chunk_costs,
                num_chunks=num_chunks,
                retries=retries,
                elapsed_seconds=time.time() - start_time,
                completed_chunks=completed_chunks,
                failed_chunks=failed_chunks,
            )

        start_time = time.time()
        logger.info(
            f"Starting chunk translation for SRT file: {request.srt_path}"
        )
        srt_text, chunks = self._prepare(request)

        if not request.pre_pass_path.exists():
            summary = build_summary(
                chunk_costs=[],
                retries=0,
                completed_chunks=0,
                failed_chunks=["pre-pass artifact missing"],
                num_chunks=len(chunks),
            )
            raise GeminiTranslationError(
                f"pre_pass.json not found at {request.pre_pass_path}; "
                "run the pre-pass stage first",
                summary,
            )
        pre_pass_result = PrePassResult.model_validate_json(
            request.pre_pass_path.read_text(encoding="utf-8")
        )

        request.chunks_cache_dir.mkdir(parents=True, exist_ok=True)
        semaphore = asyncio.Semaphore(settings.gemini_concurrency)

        async def bounded(i: int, chunk: list[SrtBlock]):
            async with semaphore:
                chunk_assets = prepare_chunk_media_assets(
                    video_path=request.video_path,
                    audio_path=request.audio_path,
                    cache_root=request.chunks_cache_dir,
                    video_key=request.audio_key,
                    chunk=chunk,
                    chunk_index=i,
                    total_chunks=len(chunks),
                    interval_seconds=settings.gemini_chunk_frame_interval_seconds,
                    max_side=settings.gemini_chunk_frame_max_side,
                    intro_skip_seconds=settings.gemini_intro_skip_seconds,
                )
                return await translate_chunk(
                    self.client,
                    chunk_assets,
                    chunk,
                    i,
                    len(chunks),
                    pre_pass_result,
                )

        raw_chunk_results = await asyncio.gather(
            *[bounded(i, c) for i, c in enumerate(chunks)],
            return_exceptions=True,
        )

        chunk_results = []
        chunk_costs: list[float] = []
        total_retries = 0
        chunk_failures: list[str] = []
        for i, (chunk, result) in enumerate(zip(chunks, raw_chunk_results)):
            if isinstance(result, Exception):
                if isinstance(result, ChunkTranslationError):
                    chunk_costs.append(result.accumulated_cost)
                    total_retries += result.retries
                    logger.error(
                        f"{result.chunk_label} failed after all tasks completed: "
                        f"{result} (${result.accumulated_cost:.4f})"
                    )
                    chunk_failures.append(f"{result.chunk_label}: {result}")
                else:
                    prefix = f"[chunk {i + 1}/{len(chunks)}]"
                    from_index = chunk[0].index
                    to_index = chunk[-1].index
                    logger.error(
                        f"{prefix} Failed after all tasks completed: "
                        f"index {from_index}–{to_index}: {result}"
                    )
                    chunk_costs.append(0.0)
                    chunk_failures.append(
                        f"{prefix} index {from_index}–{to_index}: {result}"
                    )
                continue
            chunk_costs.append(result.cost)
            total_retries += result.retries
            chunk_results.append(result)

        if chunk_failures:
            summary = build_summary(
                chunk_costs=chunk_costs,
                retries=total_retries,
                completed_chunks=len(chunk_results),
                failed_chunks=chunk_failures,
                num_chunks=len(chunks),
            )
            logger.error(
                f"Translation gather failed: {summary.completed_chunks}/"
                f"{summary.num_chunks} chunks completed, {len(summary.failed_chunks)} "
                f"failed, ${summary.total_cost:.4f} spent"
            )
            raise GeminiTranslationError(
                "One or more chunks failed after all chunk tasks completed: "
                + "; ".join(chunk_failures),
                summary,
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
        request.output_path.write_text(
            serialize_srt(all_blocks), encoding="utf-8"
        )
        logger.success(f"Translation saved to: {request.output_path}")

        summary = build_summary(
            chunk_costs=chunk_costs,
            retries=total_retries,
            completed_chunks=len(chunk_results),
            failed_chunks=[],
            num_chunks=len(chunks),
        )

        logger.info(
            f"Chunk translation done: {len(chunks)} chunks, {summary.retries} retries, "
            f"${summary.total_cost:.4f}, {summary.elapsed_seconds:.1f}s "
            f"({summary.elapsed_seconds / 60:.2f} min)"
        )

        return summary
