from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from grillmaster.asr.errors import AsrError
from grillmaster.core.srt import read_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.stages import transcript

if TYPE_CHECKING:
    from tests.stages.conftest import MakeContext

    from grillmaster.project.layout import ProjectLayout


def write_response(layout: ProjectLayout, payload: object) -> None:
    layout.asr_json.parent.mkdir(parents=True)
    layout.asr_json.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")


def phrase(speaker: int, text: str, start_ms: int) -> dict[str, object]:
    """One MAI phrase, a character every 100 ms from `start_ms`."""
    return {
        "speaker": speaker,
        "text": text,
        "words": [
            {
                "text": char,
                "offsetMilliseconds": start_ms + i * 100,
                "durationMilliseconds": 100,
            }
            for i, char in enumerate(text)
        ],
    }


def test_builds_the_japanese_srt(make_context: MakeContext, layout: ProjectLayout):
    write_response(
        layout,
        {
            "durationMilliseconds": 10_000,
            "phrases": [phrase(0, "先です。", 0), phrase(1, "後です。", 2000)],
        },
    )

    transcript.STAGE.run(make_context(StageKey.TRANSCRIPT))

    assert layout.ja_srt.read_text(encoding="utf-8") == (
        "1\n00:00:00,000 --> 00:00:00,900\n先です。\n\n"
        "2\n00:00:02,000 --> 00:00:02,900\n後です。\n"
    )
    assert [block.index for block in read_srt_file(layout.ja_srt)] == [1, 2]
    assert layout.ja_voiced_srt.read_text(encoding="utf-8") == (
        "1\n00:00:00,000 --> 00:00:00,900\n[V1] 先です。\n\n"
        "2\n00:00:02,000 --> 00:00:02,900\n[V2] 後です。\n"
    )
    assert not layout.work_dir(StageKey.TRANSCRIPT).exists()


def test_a_backchannel_inside_a_sentence_is_dropped(
    make_context: MakeContext, layout: ProjectLayout
):
    write_response(
        layout,
        {
            "durationMilliseconds": 10_000,
            "phrases": [
                phrase(0, "番組とかで", 0),
                phrase(1, "はい。", 520),
                phrase(0, "会った。", 900),
            ],
        },
    )

    transcript.STAGE.run(make_context(StageKey.TRANSCRIPT))

    (block,) = read_srt_file(layout.ja_voiced_srt)
    assert block.text == "[V1] 番組とかで会った。"


def test_response_without_words_fails(make_context: MakeContext, layout: ProjectLayout):
    write_response(layout, {"durationMilliseconds": 0, "phrases": []})

    with pytest.raises(AsrError, match="timed words"):
        transcript.STAGE.run(make_context(StageKey.TRANSCRIPT))
    assert not layout.ja_srt.exists()
    assert not layout.ja_voiced_srt.exists()


def test_a_scribe_response_fails_with_the_reset_to_run(
    make_context: MakeContext, layout: ProjectLayout
):
    write_response(
        layout,
        {"words": [{"text": "はい。", "start": 0.0, "end": 0.4, "type": "word"}]},
    )

    with pytest.raises(AsrError, match=r"not a MAI-Transcribe-2 response.*--from asr"):
        transcript.STAGE.run(make_context(StageKey.TRANSCRIPT))
    assert not layout.ja_srt.exists()
