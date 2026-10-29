from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.fakes import make_blocks

from grillmaster.agents.adapters.base import Media, MediaKind
from grillmaster.agents.errors import ValidationFailure
from grillmaster.agents.schema import strict_json_schema
from grillmaster.agents.task import SchemaOutput
from grillmaster.core.model_spec import Role
from grillmaster.core.srt import SrtBlock
from grillmaster.subtitles.finalize import clean_text as finalize_text
from grillmaster.translate.chunk import (
    ChunkLine,
    ChunkTranslation,
    build_chunk_task,
    chunk_validator,
    clean_text,
    merge_chunks,
    rebuild_blocks,
    task_name,
    unwrap_quoted_chunk,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from grillmaster.core.tool_session import ToolSession
    from grillmaster.translate.chunker import Chunk
    from grillmaster.translate.inputs import ChunkInputs


def _translation(*lines: tuple[int, str]) -> ChunkTranslation:
    return ChunkTranslation(
        blocks=[ChunkLine(index=index, text=text) for index, text in lines]
    )


def _failure(chunk: Chunk, translation: ChunkTranslation) -> str:
    with pytest.raises(ValidationFailure) as caught:
        chunk_validator(chunk)(translation)
    return str(caught.value)


def test_schema_is_native_schema_compatible() -> None:
    schema = strict_json_schema(ChunkTranslation)
    assert schema["required"] == ["blocks"]


def test_validator_accepts_full_coverage_in_any_order(chunk: Chunk) -> None:
    chunk_validator(chunk)(_translation((3, "三"), (1, "一"), (2, "二")))


def test_validator_lists_missing_indexes(chunk: Chunk) -> None:
    message = _failure(chunk, _translation((1, "一")))
    assert "缺少 index：2, 3" in message


def test_validator_lists_duplicate_and_unexpected_indexes(chunk: Chunk) -> None:
    message = _failure(
        chunk, _translation((1, "一"), (2, "二"), (2, "又二"), (3, "三"), (9, "九"))
    )
    assert "重複的 index：2" in message
    assert "不屬於本區段的 index：9" in message
    assert "缺少" not in message


@pytest.mark.parametrize("text", ["", "   ", "-", "- \n-", "\n\n"])
def test_validator_rejects_empty_text(chunk: Chunk, text: str) -> None:
    message = _failure(chunk, _translation((1, "一"), (2, text), (3, "三")))
    assert "text 為空的 index：2" in message


def test_rebuild_restores_source_timecodes_and_order(chunk: Chunk) -> None:
    blocks = rebuild_blocks(chunk, _translation((3, "三"), (1, "一"), (2, "二")))
    assert blocks == [
        SrtBlock(source.index, source.timecode, text)
        for source, text in zip(chunk.blocks, ["一", "二", "三"], strict=True)
    ]


@pytest.mark.parametrize(
    ("text", "cleaned"),
    [
        ("- 給我適可而止。\n-", "- 給我適可而止。"),
        ("- 第一人。\n- 第二人。", "- 第一人。\n- 第二人。"),
        ("上\n\n下", "上\n下"),
        ("- \n-", ""),
        ("[V2] 我很喜歡", "我很喜歡"),
        ("-[V1] 才不是\n-[V3] 是啦", "-才不是\n-是啦"),
        ("[V2]", ""),
    ],
)
def test_clean_text_drops_voice_tags_dash_only_and_blank_lines(
    text: str, cleaned: str
) -> None:
    assert clean_text(text) == cleaned


def test_rebuild_cleans_dangling_speaker_dashes(chunk: Chunk) -> None:
    blocks = rebuild_blocks(
        chunk, _translation((1, "- 給我適可而止。\n-"), (2, "二"), (3, "三"))
    )
    assert blocks[0].text == "- 給我適可而止。"


def test_a_mostly_wrapped_chunk_is_unwrapped() -> None:
    texts = {
        1: "「解散了。」",
        2: "「『拜託了』這樣」",
        3: "「畢竟，\n還以為」",
        4: "四",
    }
    assert unwrap_quoted_chunk(texts) == {
        1: "解散了。",
        2: "「拜託了」這樣",
        3: "畢竟，\n還以為",
        4: "四",
    }


@pytest.mark.parametrize(
    "texts",
    [
        pytest.param(
            {1: "「解散了。」", 2: "「對」", 3: "三", 4: "四"}, id="half-wrapped"
        ),
        pytest.param(
            {1: "「A」和「B」", 2: "「A」和「B」", 3: "「C」"},
            id="separate-quotes-are-not-a-wrap",
        ),
        pytest.param({1: "「」", 2: "「」", 3: "「」"}, id="bare-quotes"),
    ],
)
def test_a_chunk_quoting_some_lines_is_kept(texts: dict[int, str]) -> None:
    assert unwrap_quoted_chunk(texts) == texts


def test_rebuild_unwraps_a_wrapped_chunk(chunk: Chunk) -> None:
    blocks = rebuild_blocks(
        chunk, _translation((1, "「一」"), (2, "「二」"), (3, "「三」"))
    )
    assert [block.text for block in blocks] == ["一", "二", "三"]


def test_a_wrapped_exchange_ends_as_dialogue_lines(chunk: Chunk) -> None:
    blocks = rebuild_blocks(
        chunk,
        _translation((1, "「解散了。／那當然。」"), (2, "「二」"), (3, "「三」")),  # noqa: RUF001
    )
    assert finalize_text(blocks[0].text) == "-解散了\n-那當然"


def test_merge_renumbers_from_one() -> None:
    first, second = make_blocks(2), make_blocks(5)[3:]
    merged = merge_chunks([first, second])
    assert [block.index for block in merged] == [1, 2, 3, 4]
    assert [block.timecode for block in merged] == [
        block.timecode for block in [*first, *second]
    ]


def test_build_chunk_task(
    tmp_path: Path, chunk_inputs: Callable[..., ChunkInputs], tools: ToolSession
) -> None:
    inputs = chunk_inputs()
    task = build_chunk_task(
        inputs,
        session_dir=tmp_path / "session",
        workdir=tmp_path,
        tools=tools,
        attempts=3,
    )
    assert task.name == task_name(inputs.chunk) == "chunks/0001-0003"
    assert task.role is Role.CHUNK
    assert isinstance(task.output, SchemaOutput)
    assert task.media == (
        Media(MediaKind.IMAGE, tmp_path / "f1.jpg"),
        Media(MediaKind.IMAGE, tmp_path / "f2.jpg"),
        Media(MediaKind.AUDIO, tmp_path / "audio.ogg"),
    )
    assert task.tools is tools
    assert task.attempts == 3
    assert "chunk-specific audio slice" in task.instructions
    assert task.prompt.startswith("你是第 1/2 塊翻譯員")
    assert task.validate is not None
    with pytest.raises(ValidationFailure):
        task.validate(_translation((1, "一")))
    assert task.handoff is not None
    assert task.handoff.role is Role.UTILITY
    assert "no audio is available for this run" in task.handoff.instructions


def test_build_chunk_task_without_audio(
    tmp_path: Path, chunk_inputs: Callable[..., ChunkInputs], tools: ToolSession
) -> None:
    task = build_chunk_task(
        chunk_inputs(audio=False),
        session_dir=tmp_path / "session",
        workdir=tmp_path,
        tools=tools,
        attempts=1,
    )
    assert all(item.kind is MediaKind.IMAGE for item in task.media)
    assert "no audio is available for this run" in task.instructions
