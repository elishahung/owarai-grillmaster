"""Translate a single SRT chunk concurrently with timecode-first validation."""

import asyncio
import hashlib
import json

from loguru import logger
from pydantic import BaseModel

from settings import settings
from services.srt import SrtBlock
from services.inference import (
    Backend,
    backend_supports_audio,
    is_agent_backend,
    run_inference,
)
from services.inference.tools import build_frame_tool_instruction
from ..assets import ChunkMediaAssets
from ..errors import ChunkTranslationError
from .prompts import build_chunk_instruction
from .validation import canonicalize_by_position, validate_chunk_structure
from .structural_fix import fix_chunk_structure
from ..pre_pass.schema import PrePassResult, SegmentSummary


class ChunkTranslationResult(BaseModel):
    blocks: list[SrtBlock]
    cost: float
    retries: int
    from_index: int
    to_index: int


def _raw_cache_path(
    response_dir,
    from_index: int,
    to_index: int,
    user_message: str,
    backend: str,
    model: str,
):
    # Key the raw cache on backend + model too, so switching backend/model does
    # not reuse another backend's cached output for the same user_message.
    digest = hashlib.sha256(
        f"{backend}\n{model}\n{user_message}".encode("utf-8")
    ).hexdigest()[:8]
    return (
        response_dir / f"chunk_{from_index:04d}-{to_index:04d}_{digest}.raw.srt"
    )


def _fixed_cache_path(
    response_dir,
    from_index: int,
    to_index: int,
    user_message: str,
    raw_text: str,
):
    user_digest = hashlib.sha256(user_message.encode("utf-8")).hexdigest()[:8]
    raw_digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:8]
    return (
        response_dir
        / f"chunk_{from_index:04d}-{to_index:04d}_{user_digest}_{raw_digest}.fixed.srt"
    )


def _find_segment_summary(
    pre_pass: PrePassResult, from_index: int, to_index: int
) -> SegmentSummary | None:
    for segment in pre_pass.segment_summaries:
        if segment.from_index == from_index and segment.to_index == to_index:
            return segment
    return None


def _build_user_message(
    chunk: list[SrtBlock],
    chunk_index: int,
    total_chunks: int,
    pre_pass: PrePassResult,
    media_assets: ChunkMediaAssets,
) -> str:
    """Compose chunk-worker user message: briefing (global + local) + SRT slice."""
    from_index = chunk[0].index
    to_index = chunk[-1].index
    segment = _find_segment_summary(pre_pass, from_index, to_index)
    briefing = {
        "summary": pre_pass.summary,
        "characters": [c.model_dump() for c in pre_pass.characters],
        "proper_nouns": pre_pass.proper_nouns,
        "glossary": pre_pass.glossary,
        "catchphrases": [c.model_dump() for c in pre_pass.catchphrases],
        "tone_notes": pre_pass.tone_notes,
        "segment_summary": segment.summary if segment else "",
    }
    srt_slice = "\n\n".join(block.raw for block in chunk)
    frame_lines = "\n".join(
        [
            f"- {frame.timestamp_seconds:.3f}s"
            + (
                " (chunk 首幀)"
                if abs(
                    frame.timestamp_seconds
                    - media_assets.time_range.start_seconds
                )
                < 1e-6
                else ""
            )
            for frame in media_assets.frames
        ]
    )

    return (
        f"你是第 {chunk_index + 1}/{total_chunks} 塊翻譯員，負責 SRT index "
        f"{from_index}–{to_index}。\n\n"
        f"【Chunk 時間範圍】\n"
        f"{media_assets.time_range.start_seconds:.3f}s - "
        f"{media_assets.time_range.end_seconds:.3f}s\n\n"
        f"【Chunk 圖片時間點】\n"
        f"{frame_lines or '無'}\n\n"
        f"【Pre-pass 簡報】\n"
        f"{json.dumps(briefing, ensure_ascii=False, indent=2)}\n\n"
        f"【SRT 區段（index {from_index}–{to_index}，共 {len(chunk)} block）】\n"
        f"---\n{srt_slice}"
    )


def _write_chunk_manifest(
    media_assets: ChunkMediaAssets,
    user_message: str,
    raw_path,
    instruction: str,
    fixed_path=None,
):
    try:
        manifest = {}
        if media_assets.manifest_path.exists():
            manifest = json.loads(
                media_assets.manifest_path.read_text(encoding="utf-8")
            )
        manifest.update(
            {
                "instruction_sha256": hashlib.sha256(
                    instruction.encode("utf-8")
                ).hexdigest(),
                "user_message_sha256": hashlib.sha256(
                    user_message.encode("utf-8")
                ).hexdigest(),
                "raw_response_path": str(raw_path),
                "fixed_response_path": str(fixed_path) if fixed_path else None,
            }
        )
        media_assets.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning(
            f"Failed to update chunk manifest {media_assets.manifest_path}: {e}"
        )


async def translate_chunk(
    media_assets: ChunkMediaAssets,
    chunk: list[SrtBlock],
    chunk_index: int,
    total_chunks: int,
    pre_pass: PrePassResult,
) -> ChunkTranslationResult:
    """Translate one chunk with persistent media cache and response caching.

    The backend is chosen per `settings.agent_chunk_backend`; agent backends drop
    audio and translate on frames + SRT only. Output is free-form SRT (no JSON
    schema) — structural validation happens downstream, identical for every
    backend.
    """
    user_message = _build_user_message(
        chunk, chunk_index, total_chunks, pre_pass, media_assets
    )

    prefix = f"[chunk {chunk_index + 1}/{total_chunks}]"
    from_index = chunk[0].index
    to_index = chunk[-1].index
    backend = Backend(settings.agent_chunk_backend)
    has_audio = backend_supports_audio(backend)
    spec = settings.agent_chunk_model
    system_instruction = build_chunk_instruction(has_audio=has_audio)
    if is_agent_backend(backend):
        system_instruction += "\n\n" + build_frame_tool_instruction(
            media_assets.video_path,
            media_assets.time_range.start_seconds,
            media_assets.time_range.end_seconds,
            scope_label="your assigned chunk range",
            out_dir="agent_frames",
        )
    raw_path = _raw_cache_path(
        media_assets.response_dir,
        from_index,
        to_index,
        user_message,
        settings.agent_chunk_backend,
        str(spec),
    )
    source_srt = "\n\n".join(block.raw for block in chunk)

    raw_text: str | None = None
    api_cost = 0.0
    retries = 0

    if raw_path.exists():
        try:
            raw_text = raw_path.read_text(encoding="utf-8")
            logger.info(f"{prefix} Raw cache hit: {raw_path.name}")
        except OSError as e:
            logger.warning(
                f"{prefix} Raw cache read failed ({e}); re-translating"
            )

    if raw_text is None:
        max_retries = settings.chunk_max_retries
        last_error: Exception | None = None
        images = [frame.path for frame in media_assets.frames]
        # Gate audio on the backend's capability, not just the cached asset, so
        # an agent backend never receives a lingering audio segment.
        audio = (
            [media_assets.audio.path]
            if (has_audio and media_assets.audio)
            else None
        )

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(
                    f"{prefix} Translating ({settings.agent_chunk_backend}) index "
                    f"{from_index}–{to_index} ({len(chunk)} blocks, "
                    f"attempt {attempt}/{max_retries})"
                )
                io_result = await asyncio.to_thread(
                    run_inference,
                    backend=backend,
                    system_prompt=system_instruction,
                    prompt=user_message,
                    images=images,
                    audio=audio,
                    schema=None,
                    model=spec.model,
                    reasoning_effort=spec.reasoning_effort,
                )
                raw_text = io_result.text
                api_cost += io_result.cost
                retries = attempt - 1
                break
            except Exception as e:
                last_error = e
                logger.warning(f"{prefix} Attempt {attempt} failed: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2 ** (attempt - 1))

        if raw_text is None:
            logger.error(f"{prefix} All {max_retries} attempts failed")
            raise ChunkTranslationError(
                f"Chunk {chunk_index + 1}/{total_chunks} failed after "
                f"{max_retries} attempts",
                accumulated_cost=api_cost,
                retries=max_retries - 1,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                from_index=from_index,
                to_index=to_index,
            ) from last_error

        try:
            raw_path.write_text(raw_text, encoding="utf-8")
            _write_chunk_manifest(
                media_assets, user_message, raw_path, system_instruction
            )
        except OSError as e:
            logger.warning(
                f"{prefix} Failed to write raw cache {raw_path.name}: {e}"
            )

    tolerance = settings.chunk_missing_block_tolerance
    fixed_path = _fixed_cache_path(
        media_assets.response_dir, from_index, to_index, user_message, raw_text
    )
    if fixed_path.exists():
        try:
            fixed_text = fixed_path.read_text(encoding="utf-8")
            blocks = validate_chunk_structure(chunk, fixed_text, tolerance)
            logger.info(
                f"{prefix} Fixed cache hit: {len(blocks)} blocks from "
                f"{fixed_path.name}"
            )
            return ChunkTranslationResult(
                blocks=blocks,
                cost=api_cost,
                retries=retries,
                from_index=from_index,
                to_index=to_index,
            )
        except (OSError, ValueError) as e:
            logger.warning(
                f"{prefix} Fixed cache unusable ({e}); re-running fix"
            )

    try:
        blocks = validate_chunk_structure(chunk, raw_text, tolerance)
    except ValueError as validation_error:
        error_str = str(validation_error)
    else:
        logger.success(
            f"{prefix} Completed {len(blocks)} blocks "
            f"(${api_cost:.4f}, retries={retries})"
        )
        return ChunkTranslationResult(
            blocks=blocks,
            cost=api_cost,
            retries=retries,
            from_index=from_index,
            to_index=to_index,
        )

    # Raw output failed validation. Try the one cheap in-process fast-path
    # first: when block counts already match, reassign the source skeleton by
    # physical order — no agent needed for a plain index/timecode drift.
    fixed_text: str | None = None
    candidate = canonicalize_by_position(source_srt, raw_text)
    if candidate is not None:
        try:
            blocks = validate_chunk_structure(chunk, candidate, tolerance)
        except ValueError as positional_error:
            logger.warning(
                f"{prefix} Positional fast-path failed: {positional_error}"
            )
        else:
            fixed_text = candidate
            logger.success(
                f"{prefix} Positional fast-path succeeded; {len(blocks)} blocks"
            )

    # Otherwise hand it to the agent, which self-validates until it passes.
    if fixed_text is None:
        logger.warning(
            f"{prefix} Raw output failed validation: {error_str}. "
            f"Running agent fix layer."
        )
        workspace_dir = (
            media_assets.response_dir
            / f"chunk_{from_index:04d}-{to_index:04d}_fix"
        )
        try:
            fixed_text = await fix_chunk_structure(
                source_srt,
                raw_text,
                error_str,
                workspace_dir,
                tolerance,
                prefix,
            )
        except Exception as fix_error:
            raise ChunkTranslationError(
                f"Fix layer failed ({fix_error}); original: {error_str}",
                accumulated_cost=api_cost,
                retries=retries,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                from_index=from_index,
                to_index=to_index,
            ) from fix_error
        blocks = validate_chunk_structure(chunk, fixed_text, tolerance)

    # Persist whichever fix (positional or agent) produced a valid result.
    try:
        fixed_path.write_text(fixed_text, encoding="utf-8")
        _write_chunk_manifest(
            media_assets,
            user_message,
            raw_path,
            system_instruction,
            fixed_path=fixed_path,
        )
    except OSError as e:
        logger.warning(
            f"{prefix} Failed to write fixed cache {fixed_path.name}: {e}"
        )

    logger.success(
        f"{prefix} Fix succeeded; {len(blocks)} blocks "
        f"(${api_cost:.4f}, retries={retries})"
    )
    return ChunkTranslationResult(
        blocks=blocks,
        cost=api_cost,
        retries=retries,
        from_index=from_index,
        to_index=to_index,
    )
