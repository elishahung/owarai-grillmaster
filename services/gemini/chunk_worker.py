"""Translate a single SRT chunk concurrently with timecode-first validation."""

import asyncio
import hashlib
import json

from google import genai
from loguru import logger
from pydantic import BaseModel

from settings import settings
from services.chunk_fix import (
    canonicalize_by_position,
    fix_chunk_structure,
    validate_chunk_structure,
)
from services.srt import SrtBlock
from .assets import ChunkMediaAssets, media_refs_to_parts
from .cli import run_gemini_cli
from .cost import calculate_cost
from .errors import ChunkTranslationError
from .instructions import chunk_instruction
from .pre_pass import PrePassResult, SegmentSummary


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
                    chunk_instruction.encode("utf-8")
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


def _chunk_config() -> "genai.types.GenerateContentConfig":
    """Shared api-backend generation config (safety off, thinking on)."""
    thinking_level = genai.types.ThinkingLevel[settings.gemini_thinking_level]
    return genai.types.GenerateContentConfig(
        system_instruction=chunk_instruction,
        safety_settings=[
            genai.types.SafetySetting(
                category=cat,
                threshold=genai.types.HarmBlockThreshold.BLOCK_NONE,
            )
            for cat in (
                genai.types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                genai.types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                genai.types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                genai.types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
            )
        ],
        thinking_config=genai.types.ThinkingConfig(
            thinking_level=thinking_level
        ),
    )


async def _translate_via_api(
    client: genai.Client,
    model: str,
    user_message: str,
    media_parts: list,
) -> tuple[str, float]:
    """One genai API translation attempt. Returns (raw_text, cost)."""
    response = await client.aio.models.generate_content(
        model=model,
        contents=[*media_parts, user_message],
        config=_chunk_config(),
    )
    cost = calculate_cost(response.usage_metadata, model)
    finish_reason = (
        response.candidates[0].finish_reason if response.candidates else None
    )
    if finish_reason != genai.types.FinishReason.STOP:
        raise RuntimeError(
            f"Non-STOP finish reason: {finish_reason} (likely MAX_TOKENS)"
        )
    return response.text or "", cost


async def _translate_via_cli(
    model: str, user_message: str, media_assets: ChunkMediaAssets
) -> str:
    """One Gemini CLI translation attempt. Returns raw_text (subscription, no cost).

    Output is free-form SRT (no JSON schema); structural validation happens
    downstream, identical to the api path.
    """
    prompt = f"{chunk_instruction}\n\n{user_message}"
    media_files = [
        media_assets.audio.path,
        *[frame.path for frame in media_assets.frames],
    ]
    cli_result = await asyncio.to_thread(
        run_gemini_cli,
        prompt,
        model=model,
        media_files=media_files,
        schema=None,
    )
    return cli_result.response


async def translate_chunk(
    client: genai.Client | None,
    media_assets: ChunkMediaAssets,
    chunk: list[SrtBlock],
    chunk_index: int,
    total_chunks: int,
    pre_pass: PrePassResult,
) -> ChunkTranslationResult:
    """Translate one chunk with persistent media cache and response caching."""
    user_message = _build_user_message(
        chunk, chunk_index, total_chunks, pre_pass, media_assets
    )

    prefix = f"[chunk {chunk_index + 1}/{total_chunks}]"
    from_index = chunk[0].index
    to_index = chunk[-1].index
    backend = settings.chunk_gemini_backend
    model = settings.chunk_gemini_model
    raw_path = _raw_cache_path(
        media_assets.response_dir,
        from_index,
        to_index,
        user_message,
        backend,
        model,
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
        # Only the api backend needs inline media Parts; the cli backend stages
        # the raw files itself, so skip the (byte-reading) Part construction.
        media_parts = (
            media_refs_to_parts([media_assets.audio, *media_assets.frames])
            if backend == "api"
            else None
        )

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(
                    f"{prefix} Translating ({backend}) index "
                    f"{from_index}–{to_index} ({len(chunk)} blocks, "
                    f"attempt {attempt}/{max_retries})"
                )
                if backend == "cli":
                    raw_text = await _translate_via_cli(
                        model, user_message, media_assets
                    )
                else:
                    raw_text, cost = await _translate_via_api(
                        client, model, user_message, media_parts
                    )
                    api_cost += cost
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
            _write_chunk_manifest(media_assets, user_message, raw_path)
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
            media_assets, user_message, raw_path, fixed_path=fixed_path
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
