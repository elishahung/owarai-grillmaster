"""Translate a normalized chat log into Traditional Chinese.

Chat translation runs after the main subtitles are finalized so the batches
can use the finished show translation as ground truth: the effective
(glossary-checked) briefing for names and terms, and the Japanese/finalized
subtitle pairs around each batch for what viewers were reacting to. Accuracy
needs are lower than the main subtitles, so instead of refine/glossary check
the batches get one whole-stream polish call that returns only the lines it
changes: it aligns names, memes and running jokes that independent batches
rendered differently, and fixes clear mistranslations.

Batch boundaries depend only on the message list. Each batch and the polish
result is a fixed-name cache: a parseable file skips the agent, so delete it
(or `grill reset`) to re-run that step. Every agent input is in the prompt
and Python writes every file, so the sessions get throwaway workdirs.
"""

from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.agents.errors import ValidationFailure
from grillmaster.agents.task import AgentJob, AgentTask, SchemaOutput
from grillmaster.core.id_coverage import id_coverage
from grillmaster.core.json_artifact import load_model, write_model
from grillmaster.core.model_spec import Role
from grillmaster.core.models import StrictModel
from grillmaster.core.prompts import join_sections, load_prompt
from grillmaster.core.timecode import format_clock
from grillmaster.live_chat.schema import TranslatedChatLog, TranslatedChatMessage

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from pathlib import Path

    from grillmaster.agents.runner import AgentRunner
    from grillmaster.agents.task import AgentResult
    from grillmaster.core.briefing import Briefing
    from grillmaster.core.srt import SrtBlock
    from grillmaster.live_chat.schema import ChatLog, ChatMessage

# Upper bound on messages per agent call; `plan_batches` balances below it.
# Chat lines average ~10 characters, so a batch is a few minutes of a busy
# stream and a small prompt next to its context.
BATCH_SIZE = 150
# Subtitle context reaches this far before a batch's first message: viewers
# react to what was said seconds to a minute earlier.
SUBTITLE_LEAD_SECONDS = 90.0
SUBTITLE_TAIL_SECONDS = 10.0
# Read-only chat shown before each batch so running jokes carry over.
EARLIER_CHAT_COUNT = 20

POLISH_LABEL = "polish"

_PACKAGE = "grillmaster.live_chat"

# Code point ranges of Japanese script: kana, CJK ideographs (unified and
# extension A), and half-width katakana.
_JAPANESE_RANGES = (
    (0x3040, 0x30FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xFF66, 0xFF9D),
)


class ChatTranslateError(Exception):
    """Chat translation could not produce its output (e.g. batches failed)."""


class ChatLineTranslation(StrictModel):
    id: int
    text: str


class ChatBatchTranslation(StrictModel):
    """Structured output of one batch: one line per message id."""

    translations: list[ChatLineTranslation]


class ChatPolish(StrictModel):
    """Structured output of the polish pass: changed lines only."""

    corrections: list[ChatLineTranslation]


@dataclass(frozen=True, slots=True)
class ChatTranslationInputs:
    """What the translator reads: the normalized log and the ground truth.

    `source_subtitles` (Japanese) are paired with `finalized_subtitles` by
    position only when both have the same block count.
    """

    log: ChatLog
    briefing: Briefing
    source_subtitles: Sequence[SrtBlock]
    finalized_subtitles: Sequence[SrtBlock]


@dataclass(frozen=True, slots=True)
class ChatTranslationFiles:
    """Where the translator keeps its caches and session records.

    `batch_cache(n)` is batch `n`'s (1-based) cache file; its stem labels the
    batch's session. `session_dir(label)` is the session record directory of
    the task labelled `label` (the batch stem, or `POLISH_LABEL`).
    """

    batch_cache: Callable[[int], Path]
    polish_cache: Path
    session_dir: Callable[[str], Path]


def needs_translation(text: str) -> bool:
    """Whether a message contains Japanese script a reader may not follow.

    `www`, kaomoji, numbers and Latin-only messages pass through unchanged
    and never reach the agent.
    """
    return any(
        low <= ord(char) <= high for char in text for low, high in _JAPANESE_RANGES
    )


def plan_batches(log: ChatLog) -> list[list[ChatMessage]]:
    """Split the messages that need an agent into evenly sized batches.

    Like the subtitle chunker: N = ceil(count / BATCH_SIZE), then the
    messages are spread so batch sizes differ by at most one, instead of
    full batches plus a small remainder.
    """
    pending = [m for m in log.messages if needs_translation(m.text)]
    if not pending:
        return []
    count = math.ceil(len(pending) / BATCH_SIZE)
    base, extra = divmod(len(pending), count)
    batches: list[list[ChatMessage]] = []
    start = 0
    for index in range(count):
        size = base + (1 if index < extra else 0)
        batches.append(pending[start : start + size])
        start += size
    return batches


def translate_live_chat(
    inputs: ChatTranslationInputs,
    files: ChatTranslationFiles,
    agents: AgentRunner,
) -> TranslatedChatLog:
    """Translate every batch concurrently, polish the whole, and return the
    translated log (the caller writes it).

    Each batch is cached the moment its session is accepted, and every
    pending batch runs before a failure is raised, so a resume re-runs only
    what failed (or never finished).
    """
    log = inputs.log
    batches = plan_batches(log)
    context = _SharedContext.load(inputs)
    logger.info(
        f"Chat translation: {len(log.messages)} message(s), "
        f"{sum(map(len, batches))} to translate in {len(batches)} batch(es)"
    )

    translations: dict[int, str] = {}
    lock = threading.Lock()
    jobs: list[AgentJob[ChatBatchTranslation]] = []
    for number, batch in enumerate(batches, start=1):
        cache = files.batch_cache(number)
        cached = load_model(cache, ChatBatchTranslation)
        if cached is not None:
            logger.debug(f"Cache hit for chat batch {number}: {cache}")
            translations.update((line.id, line.text) for line in cached.translations)
            continue
        task = _batch_task(batch, log, context, cache, files)

        def accept(
            result: AgentResult[ChatBatchTranslation], cache: Path = cache
        ) -> None:
            write_model(cache, result.output)
            with lock:
                translations.update(
                    (line.id, line.text) for line in result.output.translations
                )

        jobs.append(AgentJob(task.name, prepare=lambda task=task: task, accept=accept))

    failures = agents.run_jobs(jobs)
    if failures:
        raise ChatTranslateError(
            f"{len(failures)}/{len(batches)} chat batch(es) failed:\n"
            + "\n".join(str(failure) for failure in failures)
        )

    if translations:
        polish = _polish(log, translations, context, files, agents)
        translations.update((line.id, line.text) for line in polish.corrections)

    return TranslatedChatLog(
        messages=[
            TranslatedChatMessage(
                **message.model_dump(),
                translation=translations.get(message.id, message.text),
            )
            for message in log.messages
        ]
    )


@dataclass(frozen=True, slots=True)
class _Subtitle:
    start: float
    end: float
    finalized: str
    japanese: str | None


@dataclass(frozen=True, slots=True)
class _SharedContext:
    briefing: str
    subtitles: tuple[_Subtitle, ...]

    @classmethod
    def load(cls, inputs: ChatTranslationInputs) -> _SharedContext:
        # Compacted once: it is repeated in every batch and the polish.
        briefing = json.dumps(
            inputs.briefing.prompt_dict(), ensure_ascii=False, separators=(",", ":")
        )
        return cls(
            briefing=(
                "## Program briefing (pre_pass.json)\n\n```json\n" + briefing + "\n```"
            ),
            subtitles=_paired_subtitles(
                inputs.source_subtitles, inputs.finalized_subtitles
            ),
        )


def _paired_subtitles(
    source: Sequence[SrtBlock], finalized: Sequence[SrtBlock]
) -> tuple[_Subtitle, ...]:
    """One row per finalized block, with its Japanese line when both SRTs
    have the same block count (refine/glossary check keep the structure)."""
    paired = len(source) == len(finalized)
    rows: list[_Subtitle] = []
    for position, block in enumerate(finalized):
        span = block.time_range
        rows.append(
            _Subtitle(
                start=span.start,
                end=span.end,
                finalized=" ".join(block.text.split()),
                japanese=" ".join(source[position].text.split()) if paired else None,
            )
        )
    return tuple(rows)


def _batch_task(
    batch: list[ChatMessage],
    log: ChatLog,
    context: _SharedContext,
    cache: Path,
    files: ChatTranslationFiles,
) -> AgentTask[ChatBatchTranslation]:
    expected = {message.id for message in batch}

    def validate(result: ChatBatchTranslation) -> None:
        _check_lines(result.translations, expected, require_all=True)

    return AgentTask(
        name=f"chat/{cache.stem}",
        role=Role.CHAT,
        instructions=_instructions("translate.md"),
        prompt=_batch_prompt(batch, log, context),
        session_dir=files.session_dir(cache.stem),
        workdir=None,
        output=SchemaOutput(ChatBatchTranslation),
        validate=validate,
    )


def _polish(
    log: ChatLog,
    translations: dict[int, str],
    context: _SharedContext,
    files: ChatTranslationFiles,
    agents: AgentRunner,
) -> ChatPolish:
    cached = load_model(files.polish_cache, ChatPolish)
    if cached is not None:
        logger.debug(f"Cache hit for chat polish: {files.polish_cache}")
        return cached
    lines = [
        f"{m.id} [{format_clock(m.seconds)}] {m.text} → {translations[m.id]}"
        for m in log.messages
        if m.id in translations
    ]
    allowed = set(translations)

    def validate(result: ChatPolish) -> None:
        _check_lines(result.corrections, allowed, require_all=False)

    result = agents.run(
        AgentTask(
            name=f"chat/{POLISH_LABEL}",
            role=Role.CHAT,
            instructions=_instructions("polish.md"),
            prompt=join_sections(
                context.briefing, "## Translated chat\n\n" + "\n".join(lines)
            ),
            session_dir=files.session_dir(POLISH_LABEL),
            workdir=None,
            output=SchemaOutput(ChatPolish),
            validate=validate,
        )
    )
    write_model(files.polish_cache, result.output)
    logger.info(f"Finished chat polish ({len(lines)} line(s))")
    return result.output


def _instructions(name: str) -> str:
    # Both passes share the glossary check's name-form rules.
    return join_sections(
        load_prompt(_PACKAGE, name), load_prompt(_PACKAGE, "name_form.md")
    )


def _check_lines(
    lines: list[ChatLineTranslation],
    allowed: Iterable[int],
    *,
    require_all: bool,
) -> None:
    """Reject unknown, duplicated, empty or (if required) missing ids."""
    coverage = id_coverage(
        allowed,
        ((line.id, line.text.strip()) for line in lines),
        require_all=require_all,
    )
    problems: list[str] = []
    if coverage.unknown:
        problems.append(f"unknown ids {list(coverage.unknown)}")
    if coverage.missing:
        problems.append(f"missing ids {list(coverage.missing)}")
    if coverage.duplicate:
        problems.append("duplicated ids")
    if coverage.empty:
        problems.append(f"empty text for ids {list(coverage.empty)}")
    if problems:
        raise ValidationFailure(
            "Return one non-empty line per listed id: " + "; ".join(problems)
        )


def _batch_prompt(
    batch: list[ChatMessage], log: ChatLog, context: _SharedContext
) -> str:
    window_start = batch[0].seconds - SUBTITLE_LEAD_SECONDS
    window_end = batch[-1].seconds + SUBTITLE_TAIL_SECONDS
    subtitle_lines = [
        f"[{format_clock(row.start)}] {row.japanese} → {row.finalized}"
        if row.japanese is not None
        else f"[{format_clock(row.start)}] {row.finalized}"
        for row in context.subtitles
        if row.end >= window_start and row.start <= window_end
    ]
    first_id = batch[0].id
    earlier = [
        message
        for message in log.messages[max(0, first_id - EARLIER_CHAT_COUNT) : first_id]
        if message.text
    ]
    sections = [
        context.briefing,
        "## Finalized subtitles (Japanese → Traditional Chinese)\n\n"
        + ("\n".join(subtitle_lines) or "(no dialogue in this window)"),
    ]
    if earlier:
        sections.append(
            "## Earlier chat (context only)\n\n"
            + "\n".join(f"[{format_clock(m.seconds)}] {m.text}" for m in earlier)
        )
    sections.append(
        "## Messages to translate\n\n"
        + "\n".join(f"{m.id} [{format_clock(m.seconds)}] {m.text}" for m in batch)
    )
    return join_sections(*sections)
