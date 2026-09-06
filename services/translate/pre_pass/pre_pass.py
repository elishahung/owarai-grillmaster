"""Pre-pass analysis: scan full SRT once to produce a shared briefing for chunks.

``run_pre_pass`` builds the system instruction (audio-conditioned on the
selected backend's capability) and the user message, then delegates to
``services.inference.run_inference`` with the ``PrePassResult`` schema. The
backend is chosen by ``settings.agent_prepass_model.backend`` (gemini-api /
gemini-cli / claude / codex); agent backends drop audio and run on frames +
SRT only. The
parsed result is written as the explicit ``pre_pass.json`` hand-off.
"""

import json
from pathlib import Path
from typing import Callable

from loguru import logger

from settings import settings
from services.media import MediaProcessor
from services.srt import SrtBlock
from ..assets import prepare_pre_pass_media_assets
from services.inference import (
    Backend,
    backend_supports_audio,
    is_agent_backend,
    run_inference,
)
from services.inference.tools import build_pre_pass_agent_instruction
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
    OFFICIAL_SUBTITLE_INSTRUCTION,
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


def _chunk_boundaries(chunks: list[list[SrtBlock]]) -> list[tuple[int, int]]:
    """The (from_index, to_index) range each downstream chunk worker gets."""
    return [(chunk[0].index, chunk[-1].index) for chunk in chunks]


def _boundaries_json(boundaries: list[tuple[int, int]]) -> str:
    return json.dumps(
        [{"from_index": f, "to_index": t} for f, t in boundaries],
        ensure_ascii=False,
    )


def _segment_coverage_validator(
    boundaries: list[tuple[int, int]],
) -> Callable[[PrePassResult], None]:
    """Reject a pre-pass that summarized only some of the chunk ranges.

    `segment_summaries` is a plain list in the schema, so a briefing covering
    just the opening chunk is schema-valid — and on a long episode the model
    does exactly that, leaving every other chunk to translate with no local
    context. Fed to `run_inference(validate=...)`, so the shared repair loop
    re-prompts with the ranges that are missing.
    """

    def validate(result: PrePassResult) -> None:
        covered = {
            (segment.from_index, segment.to_index)
            for segment in result.segment_summaries
        }
        missing = [b for b in boundaries if b not in covered]
        if missing:
            raise ValueError(
                f"segment_summaries 只涵蓋 {len(boundaries) - len(missing)}/"
                f"{len(boundaries)} 個 chunk 區間，每個區間都必須各有一筆。"
                "缺少下列區間，請補齊（from_index／to_index 需完全相符，"
                f"已有的區間請原樣保留）：{_boundaries_json(missing)}"
            )

    return validate


def _build_user_message(
    video_description: str | None,
    source_metadata_context: str | None,
    parent_pre_pass_context: str | None,
    official_subtitle_context: str | None,
    fixed_glossary: FixedGlossary,
    fixed_glossary_full: bool,
    srt_text: str,
    boundaries: list[tuple[int, int]],
    frame_timestamps: list[float],
) -> str:
    """Compose the pre-pass user message with hint, full SRT, and chunk ranges."""
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
    if official_subtitle_context:
        parts.append(
            "\n【官方CC字幕（僅涵蓋部分口說台詞，時間軸為參考）】\n---\n"
            f"{official_subtitle_context}"
        )
    if frame_timestamps:
        parts.append(
            "\n【代表圖片時間點（秒）】\n"
            + ", ".join(f"{timestamp:.3f}" for timestamp in frame_timestamps)
        )
    parts.append(f"\n【完整來源 SRT（ASR 產生，可能有錯）】\n---\n{srt_text}")
    # Last, right before the appended JSON Schema: behind ~100k chars of SRT
    # this requirement is what long episodes silently drop.
    parts.append(
        f"\n【Chunk 邊界】下游會將字幕切成以下 {len(boundaries)} 個 index 區間平行翻譯。"
        f"segment_summaries 必須剛好輸出 {len(boundaries)} 筆，"
        "逐一對應下列每個區間，不可只寫開頭幾段："
        f"\n{_boundaries_json(boundaries)}"
    )
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
    official_subtitle_context: str | None = None,
) -> tuple[PrePassResult, float]:
    """Run the single pre-pass call. Returns (parsed result, cost in USD).

    The backend is chosen by ``settings.agent_prepass_model``. Agent backends
    (claude/codex) cannot ingest audio, so audio extraction is skipped and the
    instruction is rendered without audio claims. Cost is 0.0 for every backend
    except gemini-api. ``segment_summaries`` coverage of the chunk boundaries is
    enforced via ``run_inference(validate=)``, so a briefing that skips ranges is
    repaired rather than accepted. Raises ``PrePassError`` on failure.
    """
    spec = settings.agent_prepass_model
    backend = Backend(spec.backend)
    has_audio = backend_supports_audio(backend)
    pre_pass_assets = prepare_pre_pass_media_assets(
        video_path=video_path,
        audio_path=audio_path,
        cache_root=pre_pass_cache_dir,
        srt_blocks=[block for chunk in chunks for block in chunk],
        interval_seconds=settings.prepass_frame_interval_seconds,
        max_side=settings.video_frame_max_side,
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
    boundaries = _chunk_boundaries(chunks)
    user_message = _build_user_message(
        video_description,
        source_metadata_context,
        parent_pre_pass_context,
        official_subtitle_context,
        fixed_glossary,
        fixed_glossary_full,
        srt_text,
        boundaries,
        frame_timestamps,
    )
    system_instruction = build_pre_pass_instruction(has_audio=has_audio)
    if source_metadata_context:
        system_instruction += f"\n\n{OFFICIAL_SOURCE_METADATA_INSTRUCTION}"
    if official_subtitle_context:
        system_instruction += f"\n\n{OFFICIAL_SUBTITLE_INSTRUCTION}"
    if fixed_glossary:
        system_instruction += (
            f"\n\n{FIXED_GLOSSARY_FULL_INSTRUCTION}"
            if fixed_glossary_full
            else f"\n\n{FIXED_GLOSSARY_INSTRUCTION}"
        )
    if parent_pre_pass_context:
        system_instruction += f"\n\n{PARENT_PRE_PASS_INSTRUCTION}"

    active_backend = spec.backend

    agent_instruction_enabled = is_agent_backend(backend)
    manifest_path = pre_pass_cache_dir / "manifest.json"

    # An existing pre_pass.json is reused as-is: no prompt/parameter matching.
    # Delete .pre_pass/ manually to force a re-run with new parameters.
    if pre_pass_path.exists():
        try:
            result = PrePassResult.model_validate_json(
                pre_pass_path.read_text(encoding="utf-8")
            )
            logger.info(f"[pre-pass] Reusing existing {pre_pass_path}")
            return result, 0.0
        except Exception as e:
            logger.warning(
                f"[pre-pass] Existing pre_pass unusable ({e}); re-running"
            )

    if parent_pre_pass_context:
        logger.info(f"[pre-pass] Parent pre-pass context injected")
    if official_subtitle_context:
        logger.info("[pre-pass] Official CC subtitle context injected")

    logger.info(
        f"[pre-pass] Backend: {active_backend} (model={spec.model}, "
        f"effort={spec.reasoning_effort}, "
        f"audio={'on' if has_audio else 'off'})"
    )
    if agent_instruction_enabled:
        last_block = chunks[-1][-1] if chunks and chunks[-1] else None
        source_end = (
            MediaProcessor.parse_timecode_line(last_block.timecode).end_seconds
            if last_block is not None
            else 0.0
        )
        system_instruction += "\n\n" + build_pre_pass_agent_instruction(
            pre_pass_cache_dir.parent,
            0.0,
            source_end,
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
            validate=_segment_coverage_validator(boundaries),
            cwd=pre_pass_cache_dir.parent,
            model=spec.model,
            reasoning_effort=spec.reasoning_effort,
            # The agent instruction invites built-in web search; make sure the
            # backend actually has the tool enabled (agent backends only).
            web_search=agent_instruction_enabled,
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
                "backend": active_backend,
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
