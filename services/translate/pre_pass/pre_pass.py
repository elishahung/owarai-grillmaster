"""Pre-pass analysis: scan full SRT once to produce a shared briefing for chunks.

``run_pre_pass`` builds the system instruction (audio-conditioned on the
selected backend's capability) and the user message, then delegates to
``services.inference.run_inference`` with the ``PrePassResult`` schema. The
backend is chosen by ``settings.agent_prepass_backend`` (gemini-api / gemini-cli /
claude / codex); agent backends drop audio and run on frames + SRT only. The
parsed result is written as the explicit ``pre_pass.json`` hand-off.
"""

import json
import hashlib
from pathlib import Path

from loguru import logger

from settings import settings
from services.srt import SrtBlock
from ..assets import prepare_pre_pass_media_assets
from services.inference import (
    Backend,
    backend_supports_audio,
    run_inference,
)
from services.inference.gemini_cli import GeminiCliQuotaError
from ..errors import PrePassError
from services.fixed_glossary import (
    FixedGlossary,
    filter_fixed_glossary,
    format_fixed_glossary_block,
    load_fixed_glossary,
)
from .prompts import (
    FIXED_GLOSSARY_FULL_INSTRUCTION,
    FIXED_GLOSSARY_INSTRUCTION,
    OFFICIAL_SOURCE_METADATA_INSTRUCTION,
    PARENT_PRE_PASS_INSTRUCTION,
    build_pre_pass_instruction,
)
from .schema import Character, Catchphrase, PrePassResult, SegmentSummary

__all__ = [
    "Character",
    "Catchphrase",
    "PrePassResult",
    "SegmentSummary",
    "run_pre_pass",
]


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


def run_pre_pass(
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

    The backend is chosen by ``settings.agent_prepass_backend``. Agent backends
    (claude/codex) cannot ingest audio, so audio extraction is skipped and the
    instruction is rendered without audio claims. Cost is 0.0 for every backend
    except gemini-api. Raises ``PrePassError`` on failure.
    """
    backend = Backend(settings.agent_prepass_backend)
    has_audio = backend_supports_audio(backend)
    pre_pass_assets = prepare_pre_pass_media_assets(
        video_path=video_path,
        audio_path=audio_path,
        cache_root=pre_pass_cache_dir,
        interval_seconds=settings.prepass_frame_interval_seconds,
        max_side=settings.prepass_frame_max_side,
        extract_audio=has_audio,
    )
    frame_timestamps = [
        frame.timestamp_seconds for frame in pre_pass_assets.frames
    ]
    fixed_glossary_full = settings.enable_prepass_full_fixed_glossary
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
    system_instruction = build_pre_pass_instruction(has_audio=has_audio)
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

    active_backend = settings.agent_prepass_backend
    spec = settings.agent_prepass_model

    prompt_digest = hashlib.sha256(
        (
            system_instruction
            + user_message
            + str(settings.prepass_frame_interval_seconds)
            + str(settings.prepass_frame_max_side)
            + active_backend
            + str(spec)
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

    logger.info(
        f"[pre-pass] Backend: {active_backend} (model={spec.model}, "
        f"effort={spec.reasoning_effort}, "
        f"audio={'on' if has_audio else 'off'})"
    )
    images = [frame.path for frame in pre_pass_assets.frames]
    # Gate audio on the backend's capability, not just on the cached asset:
    # an audio file may linger from an earlier gemini run, but an agent backend
    # must never receive it (run_inference would raise UnsupportedMediaError).
    audio = (
        [pre_pass_assets.audio.path]
        if (has_audio and pre_pass_assets.audio)
        else None
    )
    try:
        io_result = run_inference(
            backend=backend,
            system_prompt=system_instruction,
            prompt=user_message,
            images=images,
            audio=audio,
            schema=PrePassResult,
            model=spec.model,
            reasoning_effort=spec.reasoning_effort,
        )
    except GeminiCliQuotaError as e:
        logger.error(f"[pre-pass] Gemini CLI quota exhausted: {e}")
        raise PrePassError(
            f"Gemini CLI quota exhausted: {e}", accumulated_cost=0.0
        ) from e
    except Exception as e:
        logger.error(f"[pre-pass] Failed: {e}")
        raise PrePassError(f"Pre-pass failed: {e}", accumulated_cost=0.0) from e

    result = PrePassResult.model_validate_json(io_result.text)
    cost = io_result.cost
    requests = io_result.requests

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
                "backend": active_backend,
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
                "audio": (
                    pre_pass_assets.audio.model_dump(mode="json")
                    if pre_pass_assets.audio
                    else None
                ),
                "asset_manifest_path": str(pre_pass_assets.manifest_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    requests_note = f", requests: {requests}" if requests != 1 else ""
    logger.success(
        f"[pre-pass] Completed: {len(result.characters)} characters, "
        f"{len(result.proper_nouns)} proper_nouns, "
        f"{len(result.glossary)} glossary, "
        f"{len(result.catchphrases)} catchphrases, "
        f"{len(result.segment_summaries)} segment_summaries "
        f"(${cost:.4f}{requests_note})"
    )
    return result, cost
