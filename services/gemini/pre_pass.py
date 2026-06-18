"""Pre-pass analysis: scan full SRT once to produce a shared briefing for chunks.

Two thin inference clients feed a single orchestrator. ``_infer_via_api``
uses the genai SDK, which enforces the response schema natively (no retry
needed here). ``_infer_via_cli`` delegates to ``run_gemini_cli``, which owns
all CLI-side schema enforcement and repair retries internally. ``run_pre_pass``
just picks one client based on ``settings.prepass_gemini_backend`` and
writes the explicit ``pre_pass.json`` hand-off.
"""

import asyncio
import json
import hashlib
from pathlib import Path

from google import genai
from loguru import logger
from pydantic import BaseModel

from settings import settings
from services.srt import SrtBlock
from .assets import (
    FrameSpec,
    LocalMediaRef,
    media_refs_to_parts,
    prepare_pre_pass_media_assets,
)
from .cli import GeminiCliQuotaError, run_gemini_cli
from .cost import calculate_cost
from .errors import PrePassError
from services.fixed_glossary import (
    FixedGlossary,
    filter_fixed_glossary,
    format_fixed_glossary_block,
    load_fixed_glossary,
)
from .instructions import (
    FIXED_GLOSSARY_FULL_INSTRUCTION,
    FIXED_GLOSSARY_INSTRUCTION,
    OFFICIAL_SOURCE_METADATA_INSTRUCTION,
    PARENT_PRE_PASS_INSTRUCTION,
    pre_pass_instruction,
)


class Character(BaseModel):
    name_jp: str
    name_zh: str
    role_note: str


class Catchphrase(BaseModel):
    phrase_jp: str
    phrase_zh: str
    note: str


class SegmentSummary(BaseModel):
    from_index: int
    to_index: int
    summary: str


class PrePassResult(BaseModel):
    summary: str
    characters: list[Character]
    proper_nouns: dict[str, str]
    glossary: dict[str, str]
    catchphrases: list[Catchphrase]
    tone_notes: str
    segment_summaries: list[SegmentSummary]


def _build_user_message(
    video_description: str | None,
    source_metadata_context: str | None,
    parent_pre_pass_context: str | None,
    fixed_glossary: FixedGlossary,
    fixed_glossary_full: bool,
    srt_text: str,
    chunks: list[list[SrtBlock]],
    frame_timestamps: list[float],
) -> str:
    """Compose the pre-pass user message with hint, chunk ranges, and full SRT."""
    boundaries = [
        {"from_index": c[0].index, "to_index": c[-1].index} for c in chunks
    ]
    parts = ["請分析以下日本綜藝節目字幕，輸出符合 schema 的 JSON 簡報。"]
    if video_description:
        parts.append(f"\n【節目標題/資訊】\n{video_description}")
    if source_metadata_context:
        parts.append(f"\n【官方來源 Metadata】\n{source_metadata_context}")
    if parent_pre_pass_context:
        parts.append(
            "\n【上集 Pre-Pass JSON（請延續命名與術語一致性）】\n"
            f"{parent_pre_pass_context}"
        )
    glossary_block = format_fixed_glossary_block(
        fixed_glossary, full_mode=fixed_glossary_full
    )
    if glossary_block:
        parts.append(glossary_block)
    parts.append(
        "\n【Chunk 邊界】下游會將字幕切成以下 index 區間平行翻譯，請為每段輸出一個 segment_summary："
        f"\n{json.dumps(boundaries, ensure_ascii=False)}"
    )
    if frame_timestamps:
        parts.append(
            "\n【代表圖片時間點（秒）】\n"
            + ", ".join(f"{timestamp:.3f}" for timestamp in frame_timestamps)
        )
    parts.append(f"\n【完整來源 SRT（ASR 產生，可能有錯）】\n---\n{srt_text}")
    return "\n".join(parts)


async def _infer_via_api(
    client: genai.Client,
    *,
    system_instruction: str,
    user_message: str,
    audio_ref: LocalMediaRef,
    frame_refs: list[FrameSpec],
) -> tuple[PrePassResult, float, int]:
    """genai SDK client: native ``response_json_schema`` enforcement.

    No retry loop — the SDK guarantees a schema-conforming response.
    """
    thinking_level = genai.types.ThinkingLevel[settings.gemini_thinking_level]
    config = genai.types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        response_json_schema=PrePassResult.model_json_schema(),
        safety_settings=[
            genai.types.SafetySetting(
                category=genai.types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                threshold=genai.types.HarmBlockThreshold.BLOCK_NONE,
            ),
            genai.types.SafetySetting(
                category=genai.types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                threshold=genai.types.HarmBlockThreshold.BLOCK_NONE,
            ),
            genai.types.SafetySetting(
                category=genai.types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                threshold=genai.types.HarmBlockThreshold.BLOCK_NONE,
            ),
            genai.types.SafetySetting(
                category=genai.types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                threshold=genai.types.HarmBlockThreshold.BLOCK_NONE,
            ),
        ],
        thinking_config=genai.types.ThinkingConfig(
            thinking_level=thinking_level
        ),
    )
    media_parts = media_refs_to_parts([audio_ref, *frame_refs])
    response = await client.aio.models.generate_content(
        model=settings.prepass_gemini_model,
        contents=[*media_parts, user_message],
        config=config,
    )
    cost = calculate_cost(
        response.usage_metadata, settings.prepass_gemini_model
    )
    result = PrePassResult.model_validate_json(response.text or "")
    return result, cost, 1


async def _infer_via_cli(
    *,
    system_instruction: str,
    user_message: str,
    audio_ref: LocalMediaRef,
    frame_refs: list[FrameSpec],
) -> tuple[PrePassResult, float, int]:
    """Gemini CLI client. Schema enforcement/repair lives in run_gemini_cli.

    Cost is always 0.0 (subscription auth). ``requests`` is the CLI-reported
    backend request count, including its internal schema-repair attempts.
    """
    prompt = f"{system_instruction}\n\n{user_message}"
    media_files = [audio_ref.path, *[frame.path for frame in frame_refs]]
    cli_result = await asyncio.to_thread(
        run_gemini_cli,
        prompt,
        model=settings.prepass_gemini_model,
        media_files=media_files,
        schema=PrePassResult,
    )
    result = PrePassResult.model_validate_json(cli_result.response)
    return result, 0.0, cli_result.requests


async def run_pre_pass(
    client: genai.Client,
    video_description: str | None,
    srt_text: str,
    video_path: Path,
    audio_path: Path,
    chunks: list[list[SrtBlock]],
    pre_pass_path: Path,
    pre_pass_cache_dir: Path,
    source_metadata_context: str | None = None,
    parent_pre_pass_context: str | None = None,
) -> tuple[PrePassResult, float]:
    """Run the single pre-pass call. Returns (parsed result, cost in USD).

    Dispatches to the CLI client when ``settings.prepass_gemini_backend`` is
    "cli" (cost 0.0, subscription auth), otherwise the genai SDK client.
    Raises ``PrePassError`` on failure.
    """
    pre_pass_assets = prepare_pre_pass_media_assets(
        video_path=video_path,
        audio_path=audio_path,
        cache_root=pre_pass_cache_dir,
        interval_seconds=settings.prepass_frame_interval_seconds,
        max_side=settings.prepass_frame_max_side,
    )
    frame_timestamps = [
        frame.timestamp_seconds for frame in pre_pass_assets.frames
    ]
    fixed_glossary_full = settings.enable_full_fixed_glossary
    if fixed_glossary_full:
        fixed_glossary = load_fixed_glossary()
        if fixed_glossary:
            entry_count = sum(
                len(unit.entries()) for unit in fixed_glossary.talents
            ) + len(fixed_glossary.others)
            logger.info(
                f"[pre-pass] Fixed glossary: full mode, "
                f"{entry_count} entries injected"
            )
    else:
        fixed_glossary = filter_fixed_glossary(
            load_fixed_glossary(),
            video_description,
            srt_text,
            source_metadata_context,
            parent_pre_pass_context,
        )
        if fixed_glossary:
            flat = [
                *(e for unit in fixed_glossary.talents for e in unit.entries()),
                *fixed_glossary.others,
            ]
            logger.info(
                f"[pre-pass] Fixed glossary matched "
                f"{len(fixed_glossary.talents)} talent unit(s), "
                f"{len(fixed_glossary.others)} other(s): "
                + ", ".join(f"{'/'.join(aliases)}→{zh}" for aliases, zh in flat)
            )
    user_message = _build_user_message(
        video_description,
        source_metadata_context,
        parent_pre_pass_context,
        fixed_glossary,
        fixed_glossary_full,
        srt_text,
        chunks,
        frame_timestamps,
    )
    system_instruction = pre_pass_instruction
    if source_metadata_context:
        system_instruction += f"\n\n{OFFICIAL_SOURCE_METADATA_INSTRUCTION}"
    if fixed_glossary:
        system_instruction += (
            f"\n\n{FIXED_GLOSSARY_FULL_INSTRUCTION}"
            if fixed_glossary_full
            else f"\n\n{FIXED_GLOSSARY_INSTRUCTION}"
        )
    if parent_pre_pass_context:
        system_instruction += f"\n\n{PARENT_PRE_PASS_INSTRUCTION}"

    use_cli = settings.prepass_gemini_backend == "cli"
    backend = "cli" if use_cli else "api"
    active_model = settings.prepass_gemini_model

    prompt_digest = hashlib.sha256(
        (
            system_instruction
            + user_message
            + str(settings.prepass_frame_interval_seconds)
            + str(settings.prepass_frame_max_side)
            + backend
            + active_model
        ).encode("utf-8")
    ).hexdigest()
    manifest_path = pre_pass_cache_dir / "manifest.json"

    if pre_pass_path.exists() and manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("prompt_digest") == prompt_digest:
                logger.info(
                    f"[pre-pass] Cache validated by manifest {manifest_path}"
                )
                return (
                    PrePassResult.model_validate_json(
                        pre_pass_path.read_text(encoding="utf-8")
                    ),
                    0.0,
                )
        except Exception as e:
            logger.warning(f"[pre-pass] Manifest read failed: {e}")

    if parent_pre_pass_context:
        logger.info(f"[pre-pass] Parent pre-pass context injected")

    logger.info(f"[pre-pass] Backend: {backend} (model={active_model})")
    try:
        if use_cli:
            result, cost, requests = await _infer_via_cli(
                system_instruction=system_instruction,
                user_message=user_message,
                audio_ref=pre_pass_assets.audio,
                frame_refs=pre_pass_assets.frames,
            )
        else:
            result, cost, requests = await _infer_via_api(
                client,
                system_instruction=system_instruction,
                user_message=user_message,
                audio_ref=pre_pass_assets.audio,
                frame_refs=pre_pass_assets.frames,
            )
    except GeminiCliQuotaError as e:
        logger.error(f"[pre-pass] Gemini CLI quota exhausted: {e}")
        raise PrePassError(
            f"Gemini CLI quota exhausted: {e}", accumulated_cost=0.0
        ) from e
    except Exception as e:
        logger.error(f"[pre-pass] Failed: {e}")
        raise PrePassError(f"Pre-pass failed: {e}", accumulated_cost=0.0) from e

    pre_pass_path.parent.mkdir(parents=True, exist_ok=True)
    pre_pass_path.write_text(
        result.model_dump_json(indent=2),
        encoding="utf-8",
    )
    pre_pass_cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "prompt_digest": prompt_digest,
                "backend": backend,
                "instruction_sha256": hashlib.sha256(
                    system_instruction.encode("utf-8")
                ).hexdigest(),
                "user_message_sha256": hashlib.sha256(
                    user_message.encode("utf-8")
                ).hexdigest(),
                "frames": [
                    frame.model_dump(mode="json")
                    for frame in pre_pass_assets.frames
                ],
                "audio": pre_pass_assets.audio.model_dump(mode="json"),
                "asset_manifest_path": str(pre_pass_assets.manifest_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    requests_note = f", CLI requests: {requests}" if use_cli else ""
    logger.success(
        f"[pre-pass] Completed: {len(result.characters)} characters, "
        f"{len(result.proper_nouns)} proper_nouns, "
        f"{len(result.glossary)} glossary, "
        f"{len(result.catchphrases)} catchphrases, "
        f"{len(result.segment_summaries)} segment_summaries "
        f"(${cost:.4f}{requests_note})"
    )
    return result, cost
