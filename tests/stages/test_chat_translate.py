from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.fakes import FakeAgentRunner, make_briefing

from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.core.srt import SrtBlock, write_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.live_chat.schema import ChatLog, ChatMessage, TranslatedChatLog
from grillmaster.live_chat.translate import (
    ChatBatchTranslation,
    ChatLineTranslation,
    ChatPolish,
)
from grillmaster.project.layout import session_dir
from grillmaster.stages import chat_translate

if TYPE_CHECKING:
    from tests.stages.conftest import MakeContext

    from grillmaster.project.layout import ProjectLayout


@pytest.fixture
def script() -> dict[str, object]:
    return {
        "chat/batch_0001": ChatBatchTranslation(
            translations=[ChatLineTranslation(id=0, text="謝謝池田")]
        ),
        "chat/polish": ChatPolish(corrections=[]),
    }


@pytest.fixture(autouse=True)
def inputs(layout: ProjectLayout) -> None:
    write_model(
        layout.chat_messages,
        ChatLog(
            messages=[
                ChatMessage(id=0, seconds=3.0, author="@a", text="池田ありがとう"),
                ChatMessage(id=1, seconds=4.0, author="@b", text="www"),
            ]
        ),
    )
    write_model(layout.prepass_briefing, make_briefing(names=("池田（pre-pass）",)))
    write_srt_file(
        layout.ja_srt, [SrtBlock(1, "00:00:01,000 --> 00:00:02,000", "池田です")]
    )
    write_srt_file(
        layout.cht_srt, [SrtBlock(1, "00:00:01,000 --> 00:00:02,000", "我是池田")]
    )


def test_translates_against_the_finalized_subtitles_and_writes_the_deliverable(
    make_context: MakeContext, layout: ProjectLayout, agents: FakeAgentRunner
) -> None:
    chat_translate.STAGE.run(make_context(StageKey.CHAT_TRANSLATE))

    saved = read_model(layout.chat_cht_json, TranslatedChatLog)
    assert [m.translation for m in saved.messages] == ["謝謝池田", "www"]
    assert layout.chat_batch(1).is_file()
    assert layout.chat_polish.is_file()
    batch = agents.task("chat/batch_0001")
    assert "池田です → 我是池田" in batch.prompt
    workdir = layout.work_dir(StageKey.CHAT_TRANSLATE)
    assert batch.session_dir == session_dir(workdir, label="batch_0001")
    assert agents.task("chat/polish").session_dir == session_dir(
        workdir, label="polish"
    )


def test_reads_the_glossary_corrected_briefing_when_present(
    make_context: MakeContext, layout: ProjectLayout, agents: FakeAgentRunner
) -> None:
    write_model(layout.glossary_briefing, make_briefing(names=("池田（glossary）",)))

    chat_translate.STAGE.run(make_context(StageKey.CHAT_TRANSLATE))

    prompt = agents.task("chat/batch_0001").prompt
    assert "池田（glossary）" in prompt
    assert "池田（pre-pass）" not in prompt


def test_rerun_rebuilds_the_deliverable_from_the_caches(
    make_context: MakeContext, layout: ProjectLayout, agents: FakeAgentRunner
) -> None:
    chat_translate.STAGE.run(make_context(StageKey.CHAT_TRANSLATE))
    first = layout.chat_cht_json.read_text(encoding="utf-8")
    layout.chat_cht_json.unlink()

    chat_translate.STAGE.run(make_context(StageKey.CHAT_TRANSLATE))

    assert layout.chat_cht_json.read_text(encoding="utf-8") == first
    assert len(agents.tasks) == 2
