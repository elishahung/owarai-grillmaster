"""Translate a normalized chat log into Traditional Chinese.

Chat translation runs after the main subtitles are finalized so the batches
can use the finished show translation as ground truth: the (glossary-checked)
`pre_pass.json` for names and terms, and the Japanese/finalized subtitle
pairs around each batch for what viewers were reacting to. Accuracy needs
are lower than the main subtitles, so instead of refine/glossary check the
batches get one whole-stream polish call that returns only the lines it
changes: it aligns names, memes, and running jokes that independent batches
rendered differently, and fixes clear mistranslations.

Batch boundaries depend only on the message list. Each batch is cached as
`.live_chat/batches/batch_NNNN.json` and the polish result as
`.live_chat/polish.json`; a parseable cache file skips the model, so delete
it to re-run that step.
"""

import json
import math
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from pydantic import BaseModel

from services.inference import Backend, fan_out_concurrency, run_inference
from services.media import MediaProcessor
from services.progress import NoopProgressReporter
from services.srt import parse_srt
from settings import settings

from .schema import ChatLog, ChatMessage, TranslatedChatLog, TranslatedChatMessage

_PROMPT = (Path(__file__).parent / "prompts" / "translate.md").read_text(
    encoding="utf-8"
)
_POLISH_PROMPT = (Path(__file__).parent / "prompts" / "polish.md").read_text(
    encoding="utf-8"
)

# Upper bound on messages per model call; `plan_batches` balances below it.
# Chat lines average ~10 characters, so a batch is a few minutes of a busy
# stream and a small prompt next to its context.
BATCH_SIZE = 150
# Subtitle context reaches this far before a batch's first message: viewers
# react to what was said seconds to a minute earlier.
SUBTITLE_LEAD_SECONDS = 90.0
SUBTITLE_TAIL_SECONDS = 10.0
# Read-only chat shown before each batch so running jokes carry over.
EARLIER_CHAT_COUNT = 20

# Code point ranges of Japanese script: kana, CJK ideographs (unified and
# extension A), and half-width katakana.
_JAPANESE_RANGES = (
    (0x3040, 0x30FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xFF66, 0xFF9D),
)


class ChatLineTranslation(BaseModel):
    id: int
    text: str


class ChatBatchTranslation(BaseModel):
    """Schema-enforced output of one batch."""

    translations: list[ChatLineTranslation]


class ChatPolish(BaseModel):
    """Schema-enforced output of the polish pass: changed lines only."""

    corrections: list[ChatLineTranslation]


@dataclass(frozen=True)
class ChatTranslationInputs:
    """Project files the translator reads; Python owns every write."""

    messages_path: Path
    pre_pass_path: Path
    source_srt_path: Path
    finalized_srt_path: Path
    batches_dir: Path
    polish_path: Path
    output_path: Path


def needs_translation(text: str) -> bool:
    """Whether a message contains Japanese script a reader may not follow.

    `www`, kaomoji, numbers, and Latin-only messages pass through unchanged
    and never reach the model.
    """
    return any(
        low <= ord(char) <= high
        for char in text
        for low, high in _JAPANESE_RANGES
    )


def plan_batches(log: ChatLog) -> list[list[ChatMessage]]:
    """Split the messages that need a model into evenly sized batches.

    Like the subtitle chunker: N = ceil(count / BATCH_SIZE), then the
    messages are spread so batch sizes differ by at most one, instead of
    full batches plus a small remainder.
    """
    pending = [m for m in log.messages if needs_translation(m.text)]
    if not pending:
        return []
    count = math.ceil(len(pending) / BATCH_SIZE)
    base, extra = divmod(len(pending), count)
    batches = []
    start = 0
    for index in range(count):
        size = base + (1 if index < extra else 0)
        batches.append(pending[start : start + size])
        start += size
    return batches


def translate_live_chat(
    inputs: ChatTranslationInputs,
    *,
    on_cost: Callable[[float], None],
    progress: NoopProgressReporter | None = None,
) -> TranslatedChatLog:
    """Translate every batch, polish the whole, and write the translated log.

    ``on_cost`` runs on the calling thread once per model result, so it may
    persist project state. A failed batch fails the stage after the others
    finish; their caches make the resume re-run only what failed.
    """
    progress = progress or NoopProgressReporter()
    log = ChatLog.read(inputs.messages_path)
    batches = plan_batches(log)
    context = _SharedContext.load(inputs)
    logger.info(
        f"Chat translation: {len(log.messages)} message(s), "
        f"{sum(map(len, batches))} to translate in {len(batches)} batch(es) "
        f"with {settings.chat_model}"
    )

    translations: dict[int, str] = {}
    failures: list[str] = []
    task = (
        progress.start_stage("Translating chat", total=len(batches))
        if batches
        else None
    )
    workers = fan_out_concurrency(Backend(settings.chat_model.backend))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _translate_batch, index, batch, log, context, inputs
            ): index
            for index, batch in enumerate(batches, start=1)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                result, cost = future.result()
            except Exception as error:
                logger.error(f"Chat batch {index} failed: {error}")
                failures.append(f"batch {index}: {error}")
            else:
                translations.update(
                    (line.id, line.text) for line in result.translations
                )
                on_cost(cost)
            progress.advance(task)
    progress.finish(task, "failed" if failures else "done")
    if failures:
        raise RuntimeError(
            f"{len(failures)}/{len(batches)} chat batch(es) failed: "
            + "; ".join(failures)
        )

    if translations:
        polish, cost = _polish(log, translations, context, inputs)
        on_cost(cost)
        translations.update(
            (line.id, line.text) for line in polish.corrections
        )

    translated = TranslatedChatLog(
        messages=[
            TranslatedChatMessage(
                **message.model_dump(),
                translation=translations.get(message.id, message.text),
            )
            for message in log.messages
        ]
    )
    translated.write(inputs.output_path)
    logger.success(f"Translated chat saved: {inputs.output_path}")
    return translated


@dataclass(frozen=True)
class _SharedContext:
    briefing: str
    subtitles: list[tuple[float, float, str, str | None]]

    @classmethod
    def load(cls, inputs: ChatTranslationInputs) -> "_SharedContext":
        # Compacted once: it is repeated in every batch and the polish.
        pre_pass = json.dumps(
            json.loads(inputs.pre_pass_path.read_text(encoding="utf-8")),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return cls(
            briefing=(
                "## Program briefing (pre_pass.json)\n\n```json\n"
                + pre_pass
                + "\n```"
            ),
            subtitles=_paired_subtitles(
                inputs.source_srt_path, inputs.finalized_srt_path
            ),
        )


def _paired_subtitles(
    source_srt: Path, finalized_srt: Path
) -> list[tuple[float, float, str, str | None]]:
    """Return ``(start, end, finalized, japanese)`` per finalized block.

    Japanese text is paired by index only when both files have the same
    block count (refine/glossary check keep the structure); otherwise the
    finalized text stands alone.
    """
    finalized = parse_srt(finalized_srt.read_text(encoding="utf-8-sig"))
    source = (
        parse_srt(source_srt.read_text(encoding="utf-8-sig"))
        if source_srt.exists()
        else []
    )
    paired = len(source) == len(finalized)
    rows = []
    for position, block in enumerate(finalized):
        time_range = MediaProcessor.parse_timecode_line(block.timecode)
        rows.append(
            (
                time_range.start_seconds,
                time_range.end_seconds,
                " ".join(block.text.split()),
                " ".join(source[position].text.split()) if paired else None,
            )
        )
    return rows


def _translate_batch(
    index: int,
    batch: list[ChatMessage],
    log: ChatLog,
    context: _SharedContext,
    inputs: ChatTranslationInputs,
) -> tuple[ChatBatchTranslation, float]:
    expected = {message.id for message in batch}
    return _infer_cached(
        ChatBatchTranslation,
        prompt=_batch_prompt(batch, log, context),
        validate=lambda result: _check_lines(
            result.translations, expected, require_all=True
        ),
        cache_path=inputs.batches_dir / f"batch_{index:04d}.json",
        label=f"chat batch {index} ({len(batch)} message(s))",
    )


def _polish(
    log: ChatLog,
    translations: dict[int, str],
    context: _SharedContext,
    inputs: ChatTranslationInputs,
) -> tuple[ChatPolish, float]:
    lines = [
        f"{m.id} [{_clock(m.seconds)}] {m.text} → {translations[m.id]}"
        for m in log.messages
        if m.id in translations
    ]
    return _infer_cached(
        ChatPolish,
        prompt=_join_sections(
            _POLISH_PROMPT,
            context.briefing,
            "## Translated chat\n\n" + "\n".join(lines),
        ),
        validate=lambda result: _check_lines(
            result.corrections, translations.keys(), require_all=False
        ),
        cache_path=inputs.polish_path,
        label=f"chat polish ({len(lines)} line(s))",
    )


def _infer_cached[T: BaseModel](
    schema: type[T],
    *,
    prompt: str,
    validate: Callable[[T], None],
    cache_path: Path,
    label: str,
) -> tuple[T, float]:
    """Return the cached result for ``cache_path``, or infer and cache it.

    A fixed-filename cache: a parseable file skips the model, an unreadable
    one counts as a miss and is overwritten.
    """
    if cache_path.exists():
        try:
            cached = schema.model_validate_json(
                cache_path.read_text(encoding="utf-8")
            )
        except (ValueError, OSError) as error:
            logger.warning(f"Ignoring unreadable cache ({cache_path}): {error}")
        else:
            logger.debug(f"Cache hit for {label}: {cache_path}")
            return cached, 0.0

    spec = settings.chat_model
    # cwd is None (throwaway temp dir): every input is in the prompt and
    # Python writes the cache.
    inference = run_inference(
        backend=Backend(spec.backend),
        prompt=prompt,
        schema=schema,
        validate=validate,
        model=spec.model,
        reasoning_effort=spec.reasoning_effort,
    )
    result = schema.model_validate_json(inference.text)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    logger.info(f"Finished {label}")
    return result, inference.cost


def _check_lines(
    lines: list[ChatLineTranslation],
    allowed: Iterable[int],
    *,
    require_all: bool,
) -> None:
    """Reject unknown, duplicated, empty, or (if required) missing ids."""
    allowed = set(allowed)
    returned = [line.id for line in lines]
    problems = []
    if unknown := sorted(set(returned) - allowed):
        problems.append(f"unknown ids {unknown}")
    if require_all and (missing := sorted(allowed - set(returned))):
        problems.append(f"missing ids {missing}")
    if len(returned) != len(set(returned)):
        problems.append("duplicated ids")
    if empty := [line.id for line in lines if not line.text.strip()]:
        problems.append(f"empty text for ids {empty}")
    if problems:
        raise ValueError(
            "Return one non-empty line per listed id: " + "; ".join(problems)
        )


def _batch_prompt(
    batch: list[ChatMessage], log: ChatLog, context: _SharedContext
) -> str:
    window_start = batch[0].seconds - SUBTITLE_LEAD_SECONDS
    window_end = batch[-1].seconds + SUBTITLE_TAIL_SECONDS
    subtitle_lines = [
        f"[{_clock(start)}] {japanese} → {finalized}"
        if japanese is not None
        else f"[{_clock(start)}] {finalized}"
        for start, end, finalized, japanese in context.subtitles
        if end >= window_start and start <= window_end
    ]
    first_id = batch[0].id
    earlier = [
        message
        for message in log.messages[max(0, first_id - EARLIER_CHAT_COUNT) : first_id]
        if message.text
    ]
    sections = [
        _PROMPT,
        context.briefing,
        "## Finalized subtitles (Japanese → Traditional Chinese)\n\n"
        + ("\n".join(subtitle_lines) or "(no dialogue in this window)"),
    ]
    if earlier:
        sections.append(
            "## Earlier chat (context only)\n\n"
            + "\n".join(f"[{_clock(m.seconds)}] {m.text}" for m in earlier)
        )
    sections.append(
        "## Messages to translate\n\n"
        + "\n".join(f"{m.id} [{_clock(m.seconds)}] {m.text}" for m in batch)
    )
    return _join_sections(*sections)


def _join_sections(*sections: str) -> str:
    return "\n\n".join(sections) + "\n"


def _clock(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
