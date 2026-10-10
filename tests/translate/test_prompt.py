from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.fakes import make_briefing

from grillmaster.core.prompts import load_prompt
from grillmaster.core.srt import SrtBlock
from grillmaster.core.talent import Talent
from grillmaster.translate import prompt
from grillmaster.translate.inputs import SourceContext
from grillmaster.translate.prompt import PromptSection, render_audio_template

if TYPE_CHECKING:
    from collections.abc import Callable

    from grillmaster.translate.inputs import ChunkInputs, PrepassInputs

_PREPASS_AUDIO_PHRASES = (
    "Full Source Audio",
    "listen to the audio",
    "Verify via Audio",
    "audio vibe",
    "listening to the audio",
)
_CHUNK_AUDIO_PHRASES = (
    "chunk-specific audio slice",
    "chunk audio slice",
    "as heard in the audio",
    "images and audio",
)


# --- audio fragments ---------------------------------------------------------


@pytest.mark.parametrize(
    ("stem", "phrases"),
    [("pre_pass", _PREPASS_AUDIO_PHRASES), ("chunk", _CHUNK_AUDIO_PHRASES)],
)
def test_audio_fragments_decide_the_audio_claims(
    stem: str, phrases: tuple[str, ...]
) -> None:
    with_audio = render_audio_template(stem, has_audio=True)
    without = render_audio_template(stem, has_audio=False)
    for phrase in phrases:
        assert phrase in with_audio
        assert phrase not in without
    assert "{{audio:" not in with_audio
    assert "{{audio:" not in without


def test_no_audio_variants_say_so() -> None:
    assert "No audio track is available for this run" in render_audio_template(
        "pre_pass", has_audio=False
    )
    assert "no audio is available for this run" in render_audio_template(
        "chunk", has_audio=False
    )


def test_chunk_prompt_asks_for_json_blocks() -> None:
    text = load_prompt("grillmaster.translate", "chunk.md")
    assert '{"blocks": [{"index": <int>, "text": <string>}, ...]}' in text
    assert "raw SRT" not in text


# --- pre-pass ------------------------------------------------------------------


def test_prepass_instruction_has_only_the_blocks_for_present_inputs(
    prepass_inputs: Callable[..., PrepassInputs],
) -> None:
    text = prompt.prepass_instruction(prepass_inputs())
    assert "### FIXED GLOSSARY" in text
    assert "## On-demand video frames" in text
    # The window is stated with the tool list; the pre-pass says what it is.
    assert "entire video" not in text
    assert "end of the last subtitle block" in text
    assert "Pre-pass is the anchor" in text
    assert "Use built-in web search only" in text
    for absent in (
        "### OFFICIAL SOURCE METADATA",
        "### OFFICIAL CLOSED CAPTIONS",
        "### PARENT-PROJECT PRE-PASS REFERENCE",
        "### PROGRAM-SPECIFIC INSTRUCTIONS",
    ):
        assert absent not in text


def test_prepass_instruction_adds_conditional_blocks(
    prepass_inputs: Callable[..., PrepassInputs],
) -> None:
    source = SourceContext(
        talents=(Talent(id="t1", name="浜田雅功"),),
        program_instruction="番組ルール",
        official_subtitles=(SrtBlock(1, "00:00:01,000 --> 00:00:02,000", "CC"),),
        parent_briefing=make_briefing(),
    )
    text = prompt.prepass_instruction(prepass_inputs(source=source))
    sections = [
        "### OFFICIAL SOURCE METADATA",
        "### OFFICIAL CLOSED CAPTIONS",
        "### FIXED GLOSSARY",
        "### PARENT-PROJECT PRE-PASS REFERENCE",
        "### PROGRAM-SPECIFIC INSTRUCTIONS",
        "## On-demand video frames",
        "## Agent web search",
    ]
    positions = [text.index(section) for section in sections]
    assert positions == sorted(positions)
    assert "番組ルール" in text


def test_prepass_instruction_without_audio(
    prepass_inputs: Callable[..., PrepassInputs],
) -> None:
    text = prompt.prepass_instruction(prepass_inputs(audio=False))
    assert "Full Source Audio" not in text


def test_prepass_message_puts_the_boundaries_after_the_srt(
    prepass_inputs: Callable[..., PrepassInputs],
) -> None:
    message = prompt.prepass_message(prepass_inputs())
    assert message.index(PromptSection.FULL_SRT) < message.index(
        PromptSection.CHUNK_BOUNDARIES
    )
    assert "必須剛好輸出 2 筆" in message
    assert message.endswith(
        '[{"from_index": 1, "to_index": 3}, {"from_index": 4, "to_index": 6}]'
    )
    assert f"{PromptSection.FRAME_TIMES}\n2.200" in message


def test_prepass_message_sections(
    prepass_inputs: Callable[..., PrepassInputs],
) -> None:
    source = SourceContext(
        title="番組タイトル",
        description="企画の説明",
        hint="第2回の続き",
        talents=(
            Talent(id="t1", name="浜田雅功", name_kana="はまだまさとし", roles=("MC",)),
            Talent(id="t2", name="松本人志"),
        ),
        official_subtitles=(SrtBlock(1, "00:00:01,000 --> 00:00:02,000", "公式"),),
        parent_briefing=make_briefing(summary="前回"),
    )
    message = prompt.prepass_message(prepass_inputs(source=source))
    assert f"{PromptSection.TITLE}\n番組タイトル" in message
    assert f"{PromptSection.DESCRIPTION}\n企画の説明" in message
    assert f"{PromptSection.HINT}\n第2回の続き" in message
    assert (
        f"{PromptSection.SOURCE_METADATA}\nOfficial source cast/talent metadata:\n"
        "- 浜田雅功 / はまだまさとし (MC)\n- 松本人志"
    ) in message
    assert f"{PromptSection.PARENT_BRIEFING}\n{{\n" in message
    assert '"summary": "前回"' in message
    assert (
        f"{PromptSection.OFFICIAL_CC}\n---\n1\n00:00:01,000 --> 00:00:02,000\n公式"
    ) in message
    assert "【固定詞彙表" in message


# --- chunks ----------------------------------------------------------------------


def test_chunk_instruction(chunk_inputs: Callable[..., ChunkInputs]) -> None:
    text = prompt.chunk_instruction("チャンクルール", has_audio=True)
    assert "chunk-specific audio slice" in text
    assert "the window stated for `get_frames` under 【可用工具】" in text
    assert text.index("### PROGRAM-SPECIFIC INSTRUCTIONS") < text.index(
        "## On-demand video frames"
    )
    assert "### PROGRAM-SPECIFIC" not in prompt.chunk_instruction("", has_audio=True)


def test_chunk_message(chunk_inputs: Callable[..., ChunkInputs]) -> None:
    message = prompt.chunk_message(chunk_inputs())
    assert message.startswith("你是第 1/2 塊翻譯員，負責 SRT index 1–3。")  # noqa: RUF001
    assert f"{PromptSection.CHUNK_TIME_RANGE}\n2.000s - 7.500s" in message
    # The first frame sits exactly on the chunk start.
    assert "- 2.000s (chunk 首幀)\n- 4.200s\n" in message
    assert '"segment_summary": "seg 1-3"' in message
    assert "segment_summaries" not in message
    assert message.endswith(
        "【SRT 區段（index 1–3，共 3 block）】\n---\n"  # noqa: RUF001
        "1\n00:00:02,000 --> 00:00:03,500\nline 1\n\n"
        "2\n00:00:04,000 --> 00:00:05,500\nline 2\n\n"
        "3\n00:00:06,000 --> 00:00:07,500\nline 3"
    )
    assert PromptSection.CHUNK_OFFICIAL_CC not in message


def test_chunk_message_slices_official_cc_with_padding(
    chunk_inputs: Callable[..., ChunkInputs],
) -> None:
    # The chunk spans 2.0-7.5 s, so the padded window is 0.0-9.5 s.
    official = (
        SrtBlock(1, "00:00:00,500 --> 00:00:01,000", "padded start"),
        SrtBlock(2, "00:00:05,000 --> 00:00:06,000", "inside"),
        SrtBlock(3, "00:00:09,000 --> 00:00:09,400", "padded end"),
        SrtBlock(4, "00:00:09,500 --> 00:00:09,900", "at the edge"),
        SrtBlock(5, "00:00:12,000 --> 00:00:13,000", "too late"),
    )
    message = prompt.chunk_message(
        chunk_inputs(source=SourceContext(official_subtitles=official))
    )
    section = message[message.index(PromptSection.CHUNK_OFFICIAL_CC) :]
    for text in ("padded start", "inside", "padded end"):
        assert text in section
    for text in ("at the edge", "too late"):
        assert text not in section
    assert section.index("padded end") < section.index("【SRT 區段")


@pytest.mark.parametrize("has_audio", [True, False])
def test_prepass_prompt_describes_terms_in_the_strict_schema_shape(
    has_audio: bool,
) -> None:
    # The schema is `list[TermMapping{source, target}]`, not a free-key dict.
    text = render_audio_template("pre_pass", has_audio=has_audio)
    assert "Dict mapping" not in text
    assert '"森山": "盛山"' not in text
    assert '{"source": "森山", "target": "盛山"}' in text
    for field in ("proper_nouns", "glossary"):
        assert (
            f'- **{field}**: List of `{{"source": ..., "target": ...}}` objects' in text
        )
