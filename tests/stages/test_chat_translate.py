from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.fakes import FakeAgentRunner

from grillmaster.core.briefing import Briefing, TermMapping
from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.core.srt import SrtBlock, write_srt_file
from grillmaster.core.stage_key import StageKey
from grillmaster.live_chat.schema import ChatLog, ChatMessage, TranslatedChatLog
from grillmaster.live_chat.translate import (
    ChatBatchTranslation,
    ChatLineTranslation,
    ChatPolish,
)
from grillmaster.pipeline.stage import RunOptions
from grillmaster.project.layout import session_dir
from grillmaster.stages import chat_translate

if TYPE_CHECKING:
    from tests.stages.conftest import MakeContext

    from grillmaster.agents.runner import AgentRunner
    from grillmaster.config.load import LoadedConfig
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState


def _briefing(target: str) -> Briefing:
    return Briefing(
        summary="demo",
        characters=[],
        proper_nouns=[TermMapping(source="池田", target=target)],
        glossary=[],
        catchphrases=[],
        tone_notes="",
        segment_summaries=[],
    )


@pytest.fixture
def fake_agents(loaded: LoadedConfig) -> FakeAgentRunner:
    return FakeAgentRunner(
        {
            "chat/batch_0001": ChatBatchTranslation(
                translations=[ChatLineTranslation(id=0, text="謝謝池田")]
            ),
            "chat/polish": ChatPolish(corrections=[]),
        },
        roles=loaded.config.agents.roles.specs(),
    )


@pytest.fixture
def agents(fake_agents: FakeAgentRunner) -> AgentRunner:
    return fake_agents


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
    write_model(layout.prepass_briefing, _briefing("池田（pre-pass）"))
    write_srt_file(
        layout.ja_srt, [SrtBlock(1, "00:00:01,000 --> 00:00:02,000", "池田です")]
    )
    write_srt_file(
        layout.cht_srt, [SrtBlock(1, "00:00:01,000 --> 00:00:02,000", "我是池田")]
    )


def test_translates_against_the_finalized_subtitles_and_writes_the_deliverable(
    make_context: MakeContext, layout: ProjectLayout, fake_agents: FakeAgentRunner
) -> None:
    chat_translate.STAGE.run(make_context(StageKey.CHAT_TRANSLATE))

    saved = read_model(layout.chat_cht_json, TranslatedChatLog)
    assert [m.translation for m in saved.messages] == ["謝謝池田", "www"]
    assert layout.chat_batch(1).is_file()
    assert layout.chat_polish.is_file()
    batch = fake_agents.task("chat/batch_0001")
    assert "池田です → 我是池田" in batch.prompt
    workdir = layout.work_dir(StageKey.CHAT_TRANSLATE)
    assert batch.session_dir == session_dir(workdir, label="batch_0001")
    assert fake_agents.task("chat/polish").session_dir == session_dir(
        workdir, label="polish"
    )


def test_reads_the_glossary_corrected_briefing_when_present(
    make_context: MakeContext, layout: ProjectLayout, fake_agents: FakeAgentRunner
) -> None:
    write_model(layout.glossary_briefing, _briefing("池田（glossary）"))

    chat_translate.STAGE.run(make_context(StageKey.CHAT_TRANSLATE))

    prompt = fake_agents.task("chat/batch_0001").prompt
    assert "池田（glossary）" in prompt
    assert "池田（pre-pass）" not in prompt


def test_rerun_rebuilds_the_deliverable_from_the_caches(
    make_context: MakeContext, layout: ProjectLayout, fake_agents: FakeAgentRunner
) -> None:
    chat_translate.STAGE.run(make_context(StageKey.CHAT_TRANSLATE))
    first = layout.chat_cht_json.read_text(encoding="utf-8")
    layout.chat_cht_json.unlink()

    chat_translate.STAGE.run(make_context(StageKey.CHAT_TRANSLATE))

    assert layout.chat_cht_json.read_text(encoding="utf-8") == first
    assert len(fake_agents.tasks) == 2


def test_runs_only_with_chat(state: ProjectState) -> None:
    assert chat_translate.STAGE.enabled(RunOptions(source=state.source_id, chat=True))
    assert not chat_translate.STAGE.enabled(RunOptions(source=state.source_id))


def test_definition(layout: ProjectLayout, loaded: LoadedConfig) -> None:
    assert chat_translate.STAGE.key is StageKey.CHAT_TRANSLATE
    assert chat_translate.STAGE.outputs(layout) == (layout.chat_cht_json,)
    # No chat role configured: the utility model translates chat.
    assert chat_translate.STAGE.params(loaded.config) == {
        "model": "codex/gpt-5.5/medium"
    }
