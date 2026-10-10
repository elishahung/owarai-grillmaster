from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.fakes import FakeAgentRunner, Rounds

from grillmaster.agents.errors import AgentQuotaError, ValidationFailure
from grillmaster.agents.schema import strict_json_schema
from grillmaster.agents.task import SchemaOutput
from grillmaster.core.briefing import Briefing, TermMapping
from grillmaster.core.json_artifact import read_model
from grillmaster.core.model_spec import Role
from grillmaster.core.srt import SrtBlock
from grillmaster.live_chat.schema import ChatLog, ChatMessage
from grillmaster.live_chat.translate import (
    BATCH_SIZE,
    ChatBatchTranslation,
    ChatLineTranslation,
    ChatPolish,
    ChatTranslateError,
    ChatTranslationFiles,
    ChatTranslationInputs,
    plan_batches,
    translate_live_chat,
)

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.agents.task import AgentTask

_BRIEFING = Briefing(
    summary="demo",
    characters=[],
    proper_nouns=[TermMapping(source="池田", target="池田")],
    glossary=[],
    catchphrases=[],
    tone_notes="",
    segment_summaries=[],
)
_JA = [SrtBlock(1, "00:00:01,000 --> 00:00:02,000", "池田です")]
_CHT = [SrtBlock(1, "00:00:01,000 --> 00:00:02,000", "我是池田")]


def _batch(*lines: tuple[int, str]) -> ChatBatchTranslation:
    return ChatBatchTranslation(
        translations=[ChatLineTranslation(id=i, text=text) for i, text in lines]
    )


def _polish(*lines: tuple[int, str]) -> ChatPolish:
    return ChatPolish(
        corrections=[ChatLineTranslation(id=i, text=text) for i, text in lines]
    )


def _messages(*texts: str) -> list[ChatMessage]:
    return [
        ChatMessage(id=i, seconds=3.0 + i, author=f"@{i}", text=text)
        for i, text in enumerate(texts)
    ]


def _inputs(
    messages: list[ChatMessage], finalized: list[SrtBlock] = _CHT
) -> ChatTranslationInputs:
    return ChatTranslationInputs(
        log=ChatLog(messages=messages),
        briefing=_BRIEFING,
        source_subtitles=_JA,
        finalized_subtitles=finalized,
    )


@pytest.fixture
def files(tmp_path: Path) -> ChatTranslationFiles:
    return ChatTranslationFiles(
        batch_cache=lambda n: tmp_path / "batches" / f"batch_{n:04d}.json",
        polish_cache=tmp_path / "polish.json",
        session_dir=lambda label: tmp_path / f"session_{label}",
    )


def test_batches_translate_japanese_only_then_polish_applies(
    files: ChatTranslationFiles, tmp_path: Path
) -> None:
    inputs = _inputs(_messages("池田ありがとう", "www", "すまんな池田"))
    agents = FakeAgentRunner(
        {
            "chat/batch_0001": _batch((0, "謝謝池田"), (2, "對不起池田")),
            "chat/polish": _polish((2, "抱歉啦池田")),
        }
    )

    log = translate_live_chat(inputs, files, agents)

    assert [m.translation for m in log.messages] == ["謝謝池田", "www", "抱歉啦池田"]
    assert [m.text for m in log.messages] == ["池田ありがとう", "www", "すまんな池田"]
    batch, polish = agents.task("chat/batch_0001"), agents.task("chat/polish")
    # The briefing is compacted once, term lists as objects, shared by both.
    compact = '{"summary":"demo","characters":[],"proper_nouns":{"池田":"池田"}'
    assert compact in batch.prompt
    assert compact in polish.prompt
    # Both passes carry the glossary check's name-form rules.
    assert "## Name form" in batch.instructions
    assert "## Name form" in polish.instructions
    # The batch saw the finalized subtitle paired with its Japanese line, and
    # only the messages that contain Japanese script.
    assert "[00:01] 池田です → 我是池田" in batch.prompt
    assert "0 [00:03] 池田ありがとう" in batch.prompt
    assert "www" not in batch.prompt.split("## Messages to translate")[1]
    assert "2 [00:05] すまんな池田 → 對不起池田" in polish.prompt
    assert read_model(tmp_path / "batches" / "batch_0001.json", ChatBatchTranslation)
    assert read_model(tmp_path / "polish.json", ChatPolish) == _polish(
        (2, "抱歉啦池田")
    )

    # Batch and polish caches make a re-run free.
    again = FakeAgentRunner()
    assert translate_live_chat(inputs, files, again) == log
    assert again.tasks == []


def test_tasks_are_schema_chat_calls_with_own_sessions_and_no_workdir(
    files: ChatTranslationFiles, tmp_path: Path
) -> None:
    inputs = _inputs(_messages(*(["草"] * (BATCH_SIZE + 1))))
    agents = FakeAgentRunner(
        {
            "chat/batch_0001": _echo,
            "chat/batch_0002": _echo,
            "chat/polish": _polish(),
        }
    )

    translate_live_chat(inputs, files, agents)

    tasks = agents.tasks
    assert [task.name for task in tasks] == [
        "chat/batch_0001",
        "chat/batch_0002",
        "chat/polish",
    ]
    assert [task.session_dir for task in tasks] == [
        tmp_path / "session_batch_0001",
        tmp_path / "session_batch_0002",
        tmp_path / "session_polish",
    ]
    for task in tasks:
        assert task.role is Role.CHAT
        assert task.workdir is None
        assert isinstance(task.output, SchemaOutput)
    # Later batches see the previous messages as read-only context.
    assert "## Earlier chat" not in tasks[0].prompt
    assert "## Earlier chat (context only)" in tasks[1].prompt


def _echo(task: AgentTask[object]) -> ChatBatchTranslation:
    """A batch translation echoing every listed id."""
    listed = task.prompt.split("## Messages to translate\n\n")[1].splitlines()
    return _batch(*((int(line.split()[0]), "笑死") for line in listed))


def test_outputs_are_native_schema_compatible() -> None:
    assert strict_json_schema(ChatBatchTranslation)["required"] == ["translations"]
    assert strict_json_schema(ChatPolish)["required"] == ["corrections"]


def test_batches_are_balanced_under_the_size_cap() -> None:
    def sizes(count: int) -> list[int]:
        log = ChatLog(
            messages=[
                ChatMessage(id=i, seconds=float(i), author="@a", text="池田")
                for i in range(count)
            ]
        )
        return [len(batch) for batch in plan_batches(log)]

    assert sizes(0) == []
    assert sizes(BATCH_SIZE) == [BATCH_SIZE]
    assert sizes(BATCH_SIZE + 34) == [(BATCH_SIZE + 34) // 2] * 2
    assert sizes(2 * BATCH_SIZE + 1) == [101, 100, 100]


def test_batch_missing_an_id_fails_validation(files: ChatTranslationFiles) -> None:
    inputs = _inputs(_messages("草", "きびしい"))
    agents = FakeAgentRunner({"chat/batch_0001": _batch((0, "笑死"))})

    with pytest.raises(ChatTranslateError, match="1/1 chat batch"):
        translate_live_chat(inputs, files, agents)

    validate = agents.task("chat/batch_0001").validate
    assert validate is not None
    with pytest.raises(ValidationFailure, match=r"missing ids \[1\]"):
        validate(_batch((0, "笑死")))
    with pytest.raises(ValidationFailure, match=r"unknown ids \[5\]"):
        validate(_batch((0, "a"), (1, "b"), (5, "c")))
    with pytest.raises(ValidationFailure, match="duplicated ids"):
        validate(_batch((0, "a"), (1, "b"), (1, "c")))
    with pytest.raises(ValidationFailure, match=r"empty text for ids \[1\]"):
        validate(_batch((0, "a"), (1, " ")))


def test_a_repaired_batch_is_accepted(files: ChatTranslationFiles) -> None:
    inputs = _inputs(_messages("草"))
    agents = FakeAgentRunner(
        {
            "chat/batch_0001": Rounds(_batch(), _batch((0, "笑死"))),
            "chat/polish": _polish(),
        }
    )

    log = translate_live_chat(inputs, files, agents)

    assert [m.translation for m in log.messages] == ["笑死"]


def test_polish_may_return_only_known_ids(files: ChatTranslationFiles) -> None:
    inputs = _inputs(_messages("草", "www"))
    agents = FakeAgentRunner(
        {"chat/batch_0001": _batch((0, "笑死")), "chat/polish": _polish()}
    )
    translate_live_chat(inputs, files, agents)

    validate = agents.task("chat/polish").validate
    assert validate is not None
    validate(_polish())
    validate(_polish((0, "好好笑")))
    # Pass-through messages never reached the agent, so they are not editable.
    with pytest.raises(ValidationFailure, match=r"unknown ids \[1\]"):
        validate(_polish((1, "www")))


def test_a_failed_batch_fails_after_the_others_are_cached(
    files: ChatTranslationFiles, tmp_path: Path
) -> None:
    inputs = _inputs(_messages(*(["草"] * (BATCH_SIZE + 1))))
    agents = FakeAgentRunner(
        {
            "chat/batch_0001": _echo,
            "chat/batch_0002": AgentQuotaError("429"),
        }
    )

    with pytest.raises(ChatTranslateError, match="chat/batch_0002: 429"):
        translate_live_chat(inputs, files, agents)
    assert (tmp_path / "batches" / "batch_0001.json").exists()
    assert not (tmp_path / "batches" / "batch_0002.json").exists()

    resumed = FakeAgentRunner({"chat/batch_0002": _echo, "chat/polish": _polish()})
    log = translate_live_chat(inputs, files, resumed)
    assert [task.name for task in resumed.tasks] == ["chat/batch_0002", "chat/polish"]
    assert {m.translation for m in log.messages} == {"笑死"}


def test_a_finished_batch_is_cached_before_a_later_crash(
    files: ChatTranslationFiles, tmp_path: Path
) -> None:
    inputs = _inputs(_messages(*(["草"] * (BATCH_SIZE + 1))))
    agents = FakeAgentRunner(
        {"chat/batch_0001": _echo, "chat/batch_0002": KeyboardInterrupt()}
    )

    with pytest.raises(KeyboardInterrupt):
        translate_live_chat(inputs, files, agents)
    cached = read_model(tmp_path / "batches" / "batch_0001.json", ChatBatchTranslation)
    assert {line.text for line in cached.translations} == {"笑死"}


def test_finalized_text_stands_alone_when_block_counts_differ(
    files: ChatTranslationFiles,
) -> None:
    finalized = [*_CHT, SrtBlock(2, "00:00:03,000 --> 00:00:04,000", "第二句")]
    inputs = _inputs(_messages("草"), finalized)
    agents = FakeAgentRunner(
        {"chat/batch_0001": _batch((0, "笑死")), "chat/polish": _polish()}
    )

    translate_live_chat(inputs, files, agents)

    prompt = agents.task("chat/batch_0001").prompt
    assert "[00:01] 我是池田\n[00:03] 第二句" in prompt
    assert "池田です" not in prompt


def test_chat_without_japanese_needs_no_agent(files: ChatTranslationFiles) -> None:
    agents = FakeAgentRunner()

    log = translate_live_chat(_inputs(_messages("www", "(^^)", "GG")), files, agents)

    assert [m.translation for m in log.messages] == ["www", "(^^)", "GG"]
    assert agents.tasks == []
