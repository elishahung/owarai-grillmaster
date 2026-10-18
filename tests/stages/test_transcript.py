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


def test_builds_the_japanese_srt(make_context: MakeContext, layout: ProjectLayout):
    write_response(
        layout,
        {
            "words": [
                {"text": "先です。", "start": 0.0, "end": 0.5, "speaker_id": "a"},
                {"text": "後です。", "start": 2.0, "end": 2.5, "speaker_id": "b"},
            ]
        },
    )

    transcript.STAGE.run(make_context(StageKey.TRANSCRIPT))

    assert layout.ja_srt.read_text(encoding="utf-8") == (
        "1\n00:00:00,000 --> 00:00:01,000\n先です。\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\n後です。\n"
    )
    assert [block.index for block in read_srt_file(layout.ja_srt)] == [1, 2]
    assert not layout.work_dir(StageKey.TRANSCRIPT).exists()


def test_response_without_words_fails(make_context: MakeContext, layout: ProjectLayout):
    write_response(layout, {"text": "", "words": []})

    with pytest.raises(AsrError, match="timed words"):
        transcript.STAGE.run(make_context(StageKey.TRANSCRIPT))
    assert not layout.ja_srt.exists()
