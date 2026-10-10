"""Instruction and user-message assembly for the pre-pass and chunk calls.

Wording lives in `prompts/*.md`; this module only orders the pieces.

Audio variants are fragments, not find/replace: `pre_pass.md` and `chunk.md`
mark each audio-dependent span with a `{{audio:<name>}}` slot, and
`<stem>_audio.md` / `<stem>_no_audio.md` hold one `<!-- <name> -->` section
per slot. Both fragment files must define exactly the template's slots, so a
renamed or missing slot fails loudly instead of leaving an audio claim in a
no-audio prompt.
"""

from __future__ import annotations

import json
import re
from functools import cache
from typing import TYPE_CHECKING, Final

from loguru import logger

from grillmaster.core.prompts import (
    join_sections,
    load_prompt,
    render_program_instruction,
)
from grillmaster.core.srt import serialize_srt
from grillmaster.glossary.fixed import format_fixed_glossary_block

if TYPE_CHECKING:
    from collections.abc import Sequence

    from grillmaster.core.srt import SrtBlock
    from grillmaster.translate.chunker import Chunk
    from grillmaster.translate.inputs import ChunkInputs, PrepassInputs, Talent

_SLOT = re.compile(r"\{\{audio:([a-z_]+)\}\}")
_FRAGMENT_HEADER = re.compile(r"^<!-- ([a-z_]+) -->$", re.MULTILINE)
_SCOPE_PLACEHOLDER = "{scope}"
# CC timing is broadcast-derived and only approximately aligned with the ASR
# timeline, so a chunk's CC slice keeps this margin around its time range.
OFFICIAL_SUBTITLE_PADDING_S = 2.0
_FIRST_FRAME_TOLERANCE_S = 1e-6


class PromptSection:
    """User-message section headings shared by the pre-pass and chunk calls."""

    TITLE: Final = "【節目標題】"
    DESCRIPTION: Final = "【節目說明】"
    HINT: Final = "【使用者翻譯提示】"
    SOURCE_METADATA: Final = "【官方來源 Metadata】"
    PARENT_BRIEFING: Final = "【上集 Pre-Pass JSON（請延續命名與術語一致性）】"
    OFFICIAL_CC: Final = "【官方CC字幕（僅涵蓋部分口說台詞，時間軸為參考）】"
    FRAME_TIMES: Final = "【代表圖片時間點（秒）】"
    FULL_SRT: Final = "【完整來源 SRT（ASR 產生，可能有錯）】"
    CHUNK_BOUNDARIES: Final = "【Chunk 邊界】"
    CHUNK_TIME_RANGE: Final = "【Chunk 時間範圍】"
    CHUNK_FRAME_TIMES: Final = "【Chunk 圖片時間點】"
    BRIEFING: Final = "【Pre-pass 簡報】"
    CHUNK_OFFICIAL_CC: Final = (
        "【官方CC字幕參照（僅涵蓋部分口說台詞，時間軸為近似參考）】"
    )

    @staticmethod
    def srt_slice(chunk: Chunk) -> str:
        return (
            f"【SRT 區段（index {chunk.from_index}–{chunk.to_index}，"  # noqa: RUF001
            f"共 {len(chunk.blocks)} block）】"
        )


# --- audio-conditioned templates -------------------------------------------


@cache
def _fragments(name: str) -> dict[str, str]:
    text = load_prompt(__package__, name)
    parts = _FRAGMENT_HEADER.split(text)
    if parts[0].strip():
        raise ValueError(f"{name}: text before the first fragment header")
    fragments: dict[str, str] = {}
    for slot, body in zip(parts[1::2], parts[2::2], strict=True):
        if slot in fragments:
            raise ValueError(f"{name}: fragment {slot!r} defined twice")
        fragments[slot] = body.strip()
    return fragments


def render_audio_template(stem: str, *, has_audio: bool) -> str:
    """`<stem>.md` with every slot filled from the matching fragment file."""
    template = load_prompt(__package__, f"{stem}.md")
    fragment_file = f"{stem}_audio.md" if has_audio else f"{stem}_no_audio.md"
    fragments = _fragments(fragment_file)
    slots = set(_SLOT.findall(template))
    if slots != set(fragments):
        raise ValueError(
            f"{fragment_file} defines {sorted(fragments)} but {stem}.md "
            f"has slots {sorted(slots)}"
        )
    return _SLOT.sub(lambda match: fragments[match.group(1)], template)


# --- pre-pass ---------------------------------------------------------------


def prepass_instruction(inputs: PrepassInputs) -> str:
    """Base analysis rules (audio variant per `inputs.assets.audio`), the
    conditional blocks for the inputs present, program rules, then the
    frame-tool and web-search guidance."""
    source = inputs.source
    return join_sections(
        render_audio_template("pre_pass", has_audio=inputs.assets.audio is not None),
        load_prompt(__package__, "official_source_metadata.md")
        if source.talents
        else None,
        load_prompt(__package__, "official_subtitle.md")
        if source.official_subtitles
        else None,
        load_prompt(__package__, "fixed_glossary.md"),
        load_prompt(__package__, "parent_pre_pass.md")
        if source.parent_briefing is not None
        else None,
        render_program_instruction(source.program_instruction),
        _frames_tool("the entire video"),
        load_prompt(__package__, "pre_pass_frames.md"),
        load_prompt(__package__, "pre_pass_web_search.md"),
    )


def prepass_message(inputs: PrepassInputs) -> str:
    """Program context, fixed glossary, CC, frame times, the full SRT, and
    last the chunk boundaries, so the coverage requirement sits right
    before the output point (behind ~100k chars of SRT it is what long
    episodes drop)."""
    source = inputs.source
    parts = ["請分析以下日本綜藝節目字幕，輸出符合 schema 的 JSON 簡報。"]
    if source.title:
        parts.append(f"\n{PromptSection.TITLE}\n{source.title}")
    if source.description:
        parts.append(f"\n{PromptSection.DESCRIPTION}\n{source.description}")
    if source.hint:
        parts.append(f"\n{PromptSection.HINT}\n{source.hint}")
    if source.talents:
        parts.append(
            f"\n{PromptSection.SOURCE_METADATA}\n{_talent_lines(source.talents)}"
        )
    if source.parent_briefing is not None:
        parts.append(
            f"\n{PromptSection.PARENT_BRIEFING}\n"
            f"{source.parent_briefing.render_for_prompt()}"
        )
    parts.append(format_fixed_glossary_block(inputs.fixed_glossary))
    if source.official_subtitles:
        parts.append(
            f"\n{PromptSection.OFFICIAL_CC}\n---\n"
            f"{serialize_srt(source.official_subtitles)}"
        )
    if inputs.assets.frames:
        parts.append(
            f"\n{PromptSection.FRAME_TIMES}\n"
            + ", ".join(f"{frame.time:.3f}" for frame in inputs.assets.frames)
        )
    parts.append(f"\n{PromptSection.FULL_SRT}\n---\n{serialize_srt(inputs.blocks)}")
    boundaries = [chunk.index_range for chunk in inputs.chunks]
    parts.append(
        f"\n{PromptSection.CHUNK_BOUNDARIES}下游會將字幕切成以下 {len(boundaries)} "
        f"個 index 區間平行翻譯。segment_summaries 必須剛好輸出 {len(boundaries)} 筆，"
        "逐一對應下列每個區間，不可只寫開頭幾段："
        f"\n{boundaries_json(boundaries)}"
    )
    return "\n".join(parts)


def boundaries_json(boundaries: Sequence[tuple[int, int]]) -> str:
    return json.dumps(
        [{"from_index": start, "to_index": end} for start, end in boundaries],
        ensure_ascii=False,
    )


def _talent_lines(talents: Sequence[Talent]) -> str:
    lines = ["Official source cast/talent metadata:"]
    for talent in talents:
        roles = f" ({', '.join(talent.roles)})" if talent.roles else ""
        kana = f" / {talent.name_kana}" if talent.name_kana else ""
        lines.append(f"- {talent.name}{kana}{roles}")
    return "\n".join(lines)


# --- chunks -----------------------------------------------------------------


def chunk_instruction(program_instruction: str, *, has_audio: bool) -> str:
    """Translation rules (audio variant), program rules, frame-tool guidance."""
    return join_sections(
        render_audio_template("chunk", has_audio=has_audio),
        render_program_instruction(program_instruction),
        _frames_tool("your assigned chunk range"),
    )


def chunk_message(inputs: ChunkInputs) -> str:
    """Assignment, time range, frame times, the briefing narrowed to this
    chunk's segment summary, the overlapping CC lines, and the SRT slice."""
    chunk = inputs.chunk
    span = chunk.time_range
    if inputs.briefing.segment_summary_for(*chunk.index_range) is None:
        logger.warning(
            f"[chunk {inputs.position + 1}/{inputs.total}] Briefing has no "
            f"segment summary for {chunk.from_index}-{chunk.to_index}; "
            "translating without local narrative context"
        )
    frame_lines = "\n".join(
        f"- {frame.time:.3f}s"
        + (
            " (chunk 首幀)"
            if abs(frame.time - span.start) < _FIRST_FRAME_TOLERANCE_S
            else ""
        )
        for frame in inputs.assets.frames
    )
    official = official_subtitle_slice(inputs.source.official_subtitles, chunk)
    official_section = (
        f"{PromptSection.CHUNK_OFFICIAL_CC}\n---\n"
        + "\n\n".join(block.raw for block in official)
        + "\n\n"
        if official
        else ""
    )
    srt_slice = "\n\n".join(block.raw for block in chunk.blocks)
    return (
        f"你是第 {inputs.position + 1}/{inputs.total} 塊翻譯員，負責 SRT index "
        f"{chunk.from_index}–{chunk.to_index}。\n\n"  # noqa: RUF001
        f"{PromptSection.CHUNK_TIME_RANGE}\n"
        f"{span.start:.3f}s - {span.end:.3f}s\n\n"
        f"{PromptSection.CHUNK_FRAME_TIMES}\n"
        f"{frame_lines or '無'}\n\n"
        f"{PromptSection.BRIEFING}\n"
        f"{inputs.briefing.render_for_prompt(chunk=chunk.index_range)}\n\n"
        f"{official_section}"
        f"{PromptSection.srt_slice(chunk)}\n"
        f"---\n{srt_slice}"
    )


def official_subtitle_slice(blocks: Sequence[SrtBlock], chunk: Chunk) -> list[SrtBlock]:
    """CC blocks overlapping the chunk's time range, padded on both sides."""
    window = chunk.time_range.padded(OFFICIAL_SUBTITLE_PADDING_S)
    return [block for block in blocks if block.time_range.overlaps(window)]


def _frames_tool(scope: str) -> str:
    return load_prompt(__package__, "frames_tool.md").replace(_SCOPE_PLACEHOLDER, scope)
