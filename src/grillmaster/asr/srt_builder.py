"""Convert ElevenLabs word-level ASR output into Japanese source SRT blocks.

Words are grouped into utterances (speaker change, silence, punctuation,
length), utterances into subtitle blocks (close turns become `-` dialogue),
then timings are de-overlapped and held briefly after speech ends.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING

from grillmaster.asr.errors import AsrError
from grillmaster.asr.payload import word_items
from grillmaster.core.srt import SrtBlock
from grillmaster.core.timecode import format_timecode_line

if TYPE_CHECKING:
    from grillmaster.asr.payload import AsrPayload


JAPANESE_HARD_PUNCTUATION = "。！？?!"
JAPANESE_SOFT_PUNCTUATION = "、，,：:；;"
JAPANESE_PARTICLE_BREAK_AFTER = set("をにへでとはがのもや")
# Small kana and the prolonged-sound mark are orthographically bound to
# the preceding character — they can never start a word, an utterance,
# or a wrapped line. Both hiragana and katakana variants included.
JAPANESE_BOUND_KANA = set("ぁぃぅぇぉゃゅょっゎァィゥェォャュョッヮー")
NO_SPACE_BEFORE = set("。、，,.！？?!：:；;）)]」』】》〉")
NO_SPACE_AFTER = set("（([「『【《〈")
JAPANESE_UNSAFE_SEGMENT_STARTS = {
    "を",
    "に",
    "へ",
    "で",
    "と",
    "は",
    "が",
    "の",
    "も",
    "や",
    "か",
    "な",
    "ね",
    "よ",
    "ぞ",
    "ぜ",
    "わ",
    "さ",
    "し",
    "て",
    "だ",
    "です",
    "ます",
}

# Fixed formatting rules (the tunable knobs are `SrtFormatOptions`).
MERGE_SAME_SPEAKER_GAP_S = 0.25
MAX_OVERLAPPING_BLOCK_DURATION_S = 8.0
MAX_UTTERANCES_PER_BLOCK = 5
MAX_INLINE_SHORT_UTTERANCE_CHARS = 8
MAX_ORPHAN_TAIL_CHARS = 8
MIN_SEGMENT_DURATION_S = 0.35
DRAG_FILLER_MIN_DURATION_S = 0.6
DIALOGUE_PREFIX = "-"
IGNORED_WORD_TYPES = frozenset({"audio_event"})

# Wrap scoring: a line this short (or shorter) after a break is penalized.
_VERY_SHORT_WRAP_LINE = 3
_SHORT_WRAP_LINE = 6


@dataclass(frozen=True, slots=True)
class SrtFormatOptions:
    """The tunable formatting knobs. Production uses the defaults, except
    for a model compensation from `srt_compensation.srt_options_for_model`.

    They are fine-tuned against real ASR output, not user settings; tests
    and tuning experiments override single fields. Re-validate against
    representative ASR JSON before changing a default. Rationale:

    - `max_segment_chars = 44` (was 48): at 48 (exactly two 24-char lines)
      an utterance can grow past the cap when the segmenter absorbs a
      trailing punctuation token (splitting before `。` is forbidden), and
      the wrap then renders it in 3 lines. A sweep over 3 ASR files (~1900
      blocks): 48 -> 10 three-line blocks, 44 -> 4, 42 -> 4. The remaining
      4 are >=49-char utterances without internal punctuation, intrinsic to
      rapid variety-show speech.
    - `merge_speaker_turns_gap_s = 0.05` (was 0.45): cross-speaker gaps over
      308 transitions are roughly flat in 0.01-0.10 s, with density peaks in
      [0.05, 0.10) and [0.45, inf); 0.45 merged unrelated narration into
      dialogue. Marginal merges per band: 0.01-0.05 clear back-and-forth and
      Q&A; 0.05-0.08 mostly reactions, some narration; 0.08-0.10 about half
      co-narration. The response-cue ratio (the second utterance starts with
      はい/いや/なるほど...) peaks at 0.05 (~28%). 0.01 rejects ~25 ms
      quick-fire dialogue.
    - `subtitle_hold_after_end_s = 0.5`: ASR end times match the exact
      speech end, but the eye needs a beat to finish reading; the Netflix
      Japanese guide sets a 0.5 s minimum hold (BBC/EBU: ~0.4 s).
    - `min_inter_subtitle_gap_s = 0.08` (~2 frames at 25 fps): the EBU/
      Netflix "<=2 frames or >=500 ms" rule. Blocks either butt tightly or
      keep a clear hold; back-to-back blocks get no extension, since the cap
      sits below their end and a hold never shrinks an end.
    """

    max_characters_per_line: int = 24
    max_segment_chars: int = 44
    # 0 disables duration-based splitting.
    max_segment_duration_s: float = 0.0
    segment_on_silence_longer_than_s: float = 0.7
    merge_speaker_turns_gap_s: float = 0.05
    max_lines_per_block: int = 2
    subtitle_hold_after_end_s: float = 0.5
    min_inter_subtitle_gap_s: float = 0.08
    # A silence inside an utterance longer than this counts only as
    # `inner_silence_kept_s` toward the utterance's end; 0 disables it.
    # A model compensation, off by default (see `srt_compensation`).
    inner_silence_limit_s: float = 0.0
    inner_silence_kept_s: float = 1.0


@dataclass(frozen=True)
class WordToken:
    text: str
    start: float
    end: float
    speaker_id: str | None


@dataclass
class Utterance:
    speaker_id: str | None
    start: float
    end: float
    text: str


@dataclass
class SubtitleBlock:
    start: float
    end: float
    utterances: list[Utterance]


_DEFAULT_OPTIONS = SrtFormatOptions()


def build_srt_blocks(
    payload: AsrPayload, options: SrtFormatOptions = _DEFAULT_OPTIONS
) -> list[SrtBlock]:
    """Subtitle blocks, numbered from 1, for an ElevenLabs response.

    Production passes the model's `srt_options_for_model`; other overrides
    are for tests and tuning experiments. Raises `AsrError` when the
    response has no timed words.
    """
    tokens = _extract_tokens(payload)
    if not tokens:
        raise AsrError("ElevenLabs ASR JSON does not contain timed words")

    utterances = _build_utterances(tokens, options)
    blocks = _merge_utterances_to_blocks(utterances, options)
    blocks = _merge_overlapping_blocks(blocks, options)
    for block in blocks:
        if len(block.utterances) > 1:
            block.utterances = _inline_same_speaker_utterances(
                block.utterances, options
            )
    _resolve_block_overlaps(blocks)
    _extend_subtitle_hold_times(blocks, options)
    return _render_srt(blocks, options)


def _extract_tokens(payload: AsrPayload) -> list[WordToken]:
    tokens: list[WordToken] = []
    for item in word_items(payload):
        if not isinstance(item, dict):
            continue
        word_type = item.get("type")
        if word_type in IGNORED_WORD_TYPES:
            continue
        text = str(item.get("text") or "")
        if not text:
            continue
        start = item.get("start")
        end = item.get("end")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        if end < start:
            continue
        tokens.extend(
            _split_token_if_needed(
                WordToken(
                    text=text,
                    start=float(start),
                    end=float(end),
                    speaker_id=item.get("speaker_id"),
                ),
            )
        )
    tokens.sort(key=lambda token: (token.start, token.end))
    return tokens


def _split_token_if_needed(token: WordToken) -> list[WordToken]:
    # Only split a leading hard-punct prefix off the token (e.g.
    # `。1934` → `。` + `1934`). Soft punctuation (`、`) stays attached
    # to the next char because ASR rarely emits it as a leading prefix
    # and the segment-builder handles soft splits via punctuation rules
    # downstream.
    if len(token.text) <= 1 or token.text[0] not in JAPANESE_HARD_PUNCTUATION:
        return [token]

    return [
        WordToken(
            text=token.text[0],
            start=token.start,
            end=token.start,
            speaker_id=token.speaker_id,
        ),
        WordToken(
            text=token.text[1:],
            start=token.start,
            end=token.end,
            speaker_id=token.speaker_id,
        ),
    ]


def _build_utterances(
    tokens: list[WordToken], options: SrtFormatOptions
) -> list[Utterance]:
    utterances: list[Utterance] = []
    current: list[WordToken] = []
    # `current`'s texts joined, kept up to date per token instead of
    # re-joining the whole utterance for every candidate token.
    joined = ""

    for index, token in enumerate(tokens):
        if not current:
            current.append(token)
            joined = token.text
            continue

        split_index = _choose_utterance_split_index(
            current, joined, token, tokens=tokens, token_index=index, options=options
        )
        if split_index is not None:
            utterances.append(_tokens_to_utterance(current[:split_index], options))
            current = [*current[split_index:], token]
            joined = _join_raw(current)
        elif _should_start_new_utterance(
            current,
            joined,
            token,
            tokens=tokens,
            token_index=index,
            options=options,
        ):
            utterances.append(_tokens_to_utterance(current, options))
            current = [token]
            joined = token.text
        else:
            current.append(token)
            joined = _join_text_parts(joined, token.text)

    if current:
        utterances.append(_tokens_to_utterance(current, options))

    return [utterance for utterance in utterances if utterance.text]


def _should_start_new_utterance(
    current: list[WordToken],
    joined: str,
    token: WordToken,
    *,
    tokens: list[WordToken],
    token_index: int,
    options: SrtFormatOptions,
) -> bool:
    """`joined` is `_join_raw(current)`."""
    previous = current[-1]
    if token.speaker_id != previous.speaker_id:
        return True
    unsafe_start = _is_unsafe_segment_start(token.text)
    if (
        token.start - previous.end > options.segment_on_silence_longer_than_s
        and not unsafe_start
        and not _is_short_soft_fragment(current)
        and not _would_create_short_orphan_tail(tokens, token_index)
        and not _is_dragged_single_kana(current)
    ):
        return True

    text = _normalize_spacing(joined)
    if _ends_with_split_punctuation(previous.text):
        return True
    return (
        _ends_with_soft_split_punctuation(previous.text)
        and len(text) >= options.max_characters_per_line
    )


def _choose_utterance_split_index(
    current: list[WordToken],
    joined: str,
    token: WordToken,
    *,
    tokens: list[WordToken],
    token_index: int,
    options: SrtFormatOptions,
) -> int | None:
    """`joined` is `_join_raw(current)`."""
    if token.speaker_id != current[-1].speaker_id:
        return None

    unsafe_start = _is_unsafe_segment_start(token.text)
    if unsafe_start or _would_create_short_orphan_tail(tokens, token_index):
        return None

    prospective_text = _normalize_spacing(_join_text_parts(joined, token.text))
    prospective_duration = token.end - current[0].start
    exceeds_duration = (
        options.max_segment_duration_s > 0
        and prospective_duration > options.max_segment_duration_s
    )
    exceeds_limit = (
        len(prospective_text) > options.max_segment_chars or exceeds_duration
    )
    if not exceeds_limit:
        return None

    split_index = _find_best_utterance_split_index(current)
    return split_index if split_index is not None else len(current)


def _find_best_utterance_split_index(tokens: list[WordToken]) -> int | None:
    # Walk every index from the end so that a punctuation at the
    # very last token is also considered — splitting at len(tokens)
    # emits the whole accumulated phrase and starts the next
    # utterance fresh with the incoming token.
    for index in range(len(tokens) - 1, -1, -1):
        token = tokens[index]
        if not (
            _ends_with_split_punctuation(token.text)
            or _ends_with_soft_split_punctuation(token.text)
        ):
            continue

        split_index = index + 1
        if _join_token_texts(tokens[:split_index]):
            return split_index
    return None


def _is_short_soft_fragment(tokens: list[WordToken]) -> bool:
    if not tokens or not _ends_with_soft_split_punctuation(tokens[-1].text):
        return False
    text = _join_token_texts(tokens)
    return len(text) <= MAX_ORPHAN_TAIL_CHARS


def _is_dragged_single_kana(current: list[WordToken]) -> bool:
    """Current is a single drawn-out kana (e.g. `さ`, `そ`, `あ` held
    for ≥ DRAG_FILLER_MIN_DURATION_S). Splitting would strand it as
    its own utterance; instead it should attach to the next phrase."""
    if len(current) != 1:
        return False
    text = current[0].text.strip()
    if len(text) != 1 or text in NO_SPACE_BEFORE:
        return False
    duration = current[0].end - current[0].start
    return duration >= DRAG_FILLER_MIN_DURATION_S


def _would_create_short_orphan_tail(tokens: list[WordToken], start_index: int) -> bool:
    if start_index >= len(tokens):
        return False

    speaker_id = tokens[start_index].speaker_id
    tail: list[WordToken] = []
    raw_len = 0
    for index in range(start_index, len(tokens)):
        token = tokens[index]
        if token.speaker_id != speaker_id:
            break
        tail.append(token)
        raw_len += len(token.text)
        if raw_len > MAX_ORPHAN_TAIL_CHARS:
            return False
        if _ends_with_split_punctuation(token.text):
            break

    if not tail or not _ends_with_split_punctuation(tail[-1].text):
        return False
    text = _join_token_texts(tail)
    return len(text) <= MAX_ORPHAN_TAIL_CHARS


def _tokens_to_utterance(
    tokens: list[WordToken], options: SrtFormatOptions
) -> Utterance:
    start = tokens[0].start
    end = tokens[-1].end - _excess_inner_silence(tokens, options)
    end = max(end, start + MIN_SEGMENT_DURATION_S)
    return Utterance(
        speaker_id=tokens[0].speaker_id,
        start=start,
        end=end,
        text=_join_token_texts(tokens).strip(),
    )


def _excess_inner_silence(tokens: list[WordToken], options: SrtFormatOptions) -> float:
    """How much of the utterance's inner silences `inner_silence_limit_s` drops."""
    limit = options.inner_silence_limit_s
    if limit <= 0:
        return 0.0
    kept = min(options.inner_silence_kept_s, limit)
    gaps = (after.start - before.end for before, after in pairwise(tokens))
    return sum(gap - kept for gap in gaps if gap > limit)


def _merge_utterances_to_blocks(
    utterances: list[Utterance], options: SrtFormatOptions
) -> list[SubtitleBlock]:
    blocks: list[SubtitleBlock] = []

    for utterance in utterances:
        if not blocks:
            blocks.append(
                SubtitleBlock(
                    start=utterance.start,
                    end=utterance.end,
                    utterances=[utterance],
                )
            )
            continue

        block = blocks[-1]
        if _can_merge_into_block(block, utterance, options):
            same_speaker = block.utterances[-1].speaker_id == utterance.speaker_id
            prev_ends_hard = _ends_with_split_punctuation(block.utterances[-1].text)
            if same_speaker and not prev_ends_hard:
                block.utterances[-1].text = _join_text_parts(
                    block.utterances[-1].text, utterance.text
                )
                block.utterances[-1].end = utterance.end
            else:
                # Cross-speaker, or same-speaker across hard punct —
                # keep as separate utterances so the inline pass can
                # space-join short same-speaker pairs.
                block.utterances.append(utterance)
            block.end = max(block.end, utterance.end)
        else:
            blocks.append(
                SubtitleBlock(
                    start=utterance.start,
                    end=utterance.end,
                    utterances=[utterance],
                )
            )

    return blocks


def _merge_overlapping_blocks(
    blocks: list[SubtitleBlock], options: SrtFormatOptions
) -> list[SubtitleBlock]:
    if not blocks:
        return []

    merged: list[SubtitleBlock] = [blocks[0]]
    for block in blocks[1:]:
        previous = merged[-1]
        merged_duration = max(previous.end, block.end) - previous.start
        if (
            block.start < previous.end
            and merged_duration <= MAX_OVERLAPPING_BLOCK_DURATION_S
            and len(previous.utterances) + len(block.utterances)
            <= MAX_UTTERANCES_PER_BLOCK
            and _rendered_line_count(
                _inline_same_speaker_utterances(
                    [*previous.utterances, *block.utterances], options
                ),
                options,
            )
            <= options.max_lines_per_block
        ):
            previous.utterances.extend(block.utterances)
            previous.end = max(previous.end, block.end)
        else:
            merged.append(block)
    return merged


def _resolve_block_overlaps(blocks: list[SubtitleBlock]) -> None:
    for index in range(1, len(blocks)):
        previous = blocks[index - 1]
        current = blocks[index]
        if previous.end <= current.start:
            continue

        if current.start - previous.start >= MIN_SEGMENT_DURATION_S:
            previous.end = current.start
            continue

        current.start = previous.end
        if current.end <= current.start:
            current.end = current.start + MIN_SEGMENT_DURATION_S


def _extend_subtitle_hold_times(
    blocks: list[SubtitleBlock], options: SrtFormatOptions
) -> None:
    """Extend each block's end time so subtitles linger briefly after
    speech ends. Capped at the next block's start (minus a small
    inter-subtitle gap) so we never introduce overlap; never shrinks
    an existing end time."""
    if options.subtitle_hold_after_end_s <= 0 or not blocks:
        return

    hold = options.subtitle_hold_after_end_s
    min_gap = options.min_inter_subtitle_gap_s
    for index, block in enumerate(blocks):
        desired_end = block.end + hold
        if index + 1 < len(blocks):
            cap_end = blocks[index + 1].start - min_gap
            new_end = min(desired_end, cap_end)
        else:
            new_end = desired_end
        block.end = max(block.end, new_end)


def _inline_same_speaker_utterances(
    utterances: list[Utterance], options: SrtFormatOptions
) -> list[Utterance]:
    """Return a new list with adjacent short same-speaker utterances
    inlined onto one line (e.g. 「何？ 何？ 何？」). Multi-speaker
    input is returned as a shallow copy unchanged."""
    if len({u.speaker_id for u in utterances}) != 1:
        return list(utterances)

    inlined: list[Utterance] = []
    for u in utterances:
        if inlined and _can_inline_same_speaker_utterance(inlined[-1], u, options):
            last = inlined[-1]
            inlined[-1] = Utterance(
                speaker_id=last.speaker_id,
                start=last.start,
                end=max(last.end, u.end),
                text=f"{last.text} {u.text}",
            )
        else:
            inlined.append(u)
    return inlined


def _can_inline_same_speaker_utterance(
    left: Utterance, right: Utterance, options: SrtFormatOptions
) -> bool:
    if left.speaker_id != right.speaker_id:
        return False
    if not (_is_short_inline_utterance(left) and _is_short_inline_utterance(right)):
        return False
    gap = max(0.0, right.start - left.end)
    if gap > MERGE_SAME_SPEAKER_GAP_S:
        return False
    combined_text = f"{left.text} {right.text}"
    return len(combined_text) <= options.max_characters_per_line


def _is_short_inline_utterance(utterance: Utterance) -> bool:
    text = utterance.text.strip()
    return (
        bool(text)
        and len(text) <= MAX_INLINE_SHORT_UTTERANCE_CHARS
        and _ends_with_split_punctuation(text)
    )


def _can_merge_into_block(
    block: SubtitleBlock, utterance: Utterance, options: SrtFormatOptions
) -> bool:
    gap = utterance.start - block.end
    same_speaker = block.utterances[-1].speaker_id == utterance.speaker_id
    # Two complete same-speaker sentences normally never share a block.
    # Exception: both sides are short and inline-eligible — allow the
    # merge so `_inline_same_speaker_utterances` can join them with a
    # space (e.g. 「いいんすか？ それ。」).
    if (
        same_speaker
        and _ends_with_split_punctuation(block.utterances[-1].text)
        and not _can_inline_same_speaker_utterance(
            block.utterances[-1], utterance, options
        )
    ):
        return False
    max_gap = (
        MERGE_SAME_SPEAKER_GAP_S if same_speaker else options.merge_speaker_turns_gap_s
    )
    gap = max(gap, 0)
    if gap > max_gap:
        return False
    if len(block.utterances) + 1 > MAX_UTTERANCES_PER_BLOCK:
        return False
    candidate_utterances = _inline_same_speaker_utterances(
        [*block.utterances, utterance], options
    )
    if (
        _rendered_line_count(candidate_utterances, options)
        > options.max_lines_per_block
    ):
        return False
    if (
        options.max_segment_duration_s > 0
        and utterance.end - block.start > options.max_segment_duration_s
    ):
        return False
    return _block_text_length(block) + len(utterance.text) <= options.max_segment_chars


def _render_srt(
    blocks: list[SubtitleBlock], options: SrtFormatOptions
) -> list[SrtBlock]:
    return [
        SrtBlock(
            index=index,
            timecode=format_timecode_line(block.start, block.end),
            text="\n".join(_render_block_text(block, options)),
        )
        for index, block in enumerate(blocks, start=1)
    ]


def _render_block_text(block: SubtitleBlock, options: SrtFormatOptions) -> list[str]:
    use_dialogue = len({item.speaker_id for item in block.utterances}) > 1
    rendered: list[str] = []
    for utterance in block.utterances:
        text = utterance.text.strip()
        if not text:
            continue
        for line in _wrap_text(text, options, max_lines=options.max_lines_per_block):
            if use_dialogue:
                rendered.append(f"{DIALOGUE_PREFIX}{line}")
            else:
                rendered.append(line)
    return rendered


def _rendered_line_count(utterances: list[Utterance], options: SrtFormatOptions) -> int:
    return sum(
        len(_wrap_text(utterance.text.strip(), options))
        for utterance in utterances
        if utterance.text.strip()
    )


def _wrap_text(
    text: str, options: SrtFormatOptions, *, max_lines: int | None = None
) -> list[str]:
    max_chars = options.max_characters_per_line
    if len(text) <= max_chars:
        return [text]

    lines: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        split_at = _find_wrap_index(remaining, max_chars)
        lines.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        lines.append(remaining)

    if max_lines is not None and len(lines) > max_lines:
        return _balanced_wrap(text.strip(), max_lines)
    return lines


def _balanced_wrap(text: str, max_lines: int) -> list[str]:
    """Reflow text into exactly max_lines balanced lines using the same
    break scorer as _find_wrap_index, ignoring the per-line cap. Used
    only at final render so a single over-cap utterance never shows
    more than max_lines lines."""
    if max_lines <= 1 or len(text) <= 1:
        return [text]

    n = len(text)
    target = n / max_lines
    best_index = max(1, round(target))
    best_score = float("-inf")
    for i in range(1, n):
        score = _score_wrap_break(text, i, target)
        if score > best_score or (
            score == best_score and abs(i - target) < abs(best_index - target)
        ):
            best_score = score
            best_index = i

    head = text[:best_index].strip()
    tail = text[best_index:].strip()
    if not head or not tail:
        return [text]
    return [head, *_balanced_wrap(tail, max_lines - 1)]


def _find_wrap_index(text: str, max_chars: int) -> int:
    n = len(text)
    hi = min(max_chars, n - 1)
    # When the utterance fits in two lines (n <= 2 * max_chars) keep
    # `lo = n - max_chars` so the second line also stays within the
    # cap. When it doesn't fit, line 2+ will wrap recursively, so we
    # only require a non-empty line 1 — letting the scorer pick a
    # safe break instead of greedy-cutting mid-word at max_chars.
    lo = max(1, n - max_chars) if n <= 2 * max_chars else 1
    if lo > hi:
        return max_chars

    midpoint = n / 2
    best_index = max_chars
    best_score = float("-inf")
    for i in range(lo, hi + 1):
        score = _score_wrap_break(text, i, midpoint)
        if score > best_score or (
            score == best_score and abs(i - midpoint) < abs(best_index - midpoint)
        ):
            best_score = score
            best_index = i
    return best_index


def _score_wrap_break(text: str, i: int, midpoint: float) -> float:
    line1 = text[:i]
    line2 = text[i:]
    score = 0.0

    last = line1[-1]
    ends_clause = last in JAPANESE_HARD_PUNCTUATION or last in JAPANESE_SOFT_PUNCTUATION

    # Unsafe-start penalty only when line 1 did not already terminate the
    # clause; otherwise breaking before a particle-like char is fine
    # (e.g. "...、|はたまた..." — `は` here is part of an adverb, not a
    # topic particle).
    if not ends_clause and _line_wrap_unsafe_start(line2):
        score -= 50.0

    shorter = min(len(line1), len(line2))
    if shorter <= _VERY_SHORT_WRAP_LINE:
        score -= 30.0
    elif shorter <= _SHORT_WRAP_LINE:
        score -= 5.0

    if _is_ascii_alphanum(last) and _is_ascii_alphanum(line2[0]):
        score -= 80.0

    if last in JAPANESE_HARD_PUNCTUATION:
        score += 40.0
    elif last in JAPANESE_SOFT_PUNCTUATION:
        score += 30.0
    elif last in JAPANESE_PARTICLE_BREAK_AFTER:
        score += 15.0

    score -= abs(i - midpoint) * 0.5
    return score


def _line_wrap_unsafe_start(line2: str) -> bool:
    if not line2:
        return False
    if line2[0] in NO_SPACE_BEFORE or line2[0] in JAPANESE_BOUND_KANA:
        return True
    return any(line2.startswith(u) for u in JAPANESE_UNSAFE_SEGMENT_STARTS)


def _is_ascii_alphanum(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def _join_token_texts(tokens: list[WordToken]) -> str:
    return _normalize_spacing(_join_raw(tokens))


def _join_raw(tokens: list[WordToken]) -> str:
    """The token texts joined, before spacing is normalized."""
    text = ""
    for token in tokens:
        text = _join_text_parts(text, token.text)
    return text


def _join_text_parts(left: str, right: str) -> str:
    """Japanese joining: no space, except between two ASCII alphanumerics."""
    if not left:
        return right
    if not right:
        return left
    if right[0] in NO_SPACE_BEFORE or left[-1] in NO_SPACE_AFTER:
        return left + right
    if _is_ascii_alphanum(left[-1]) and _is_ascii_alphanum(right[0]):
        return left + " " + right
    return left + right


def _normalize_spacing(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([。、，,.！？?!：:；;）)\]」』】》〉])", r"\1", text)
    text = re.sub(r"([（(\[「『【《〈])\s+", r"\1", text)
    return text.strip()


def _is_unsafe_segment_start(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    return (
        stripped[0] in NO_SPACE_BEFORE
        or stripped[0] in JAPANESE_BOUND_KANA
        or stripped in JAPANESE_UNSAFE_SEGMENT_STARTS
    )


def _ends_with_split_punctuation(text: str) -> bool:
    return bool(text and text[-1] in JAPANESE_HARD_PUNCTUATION)


def _ends_with_soft_split_punctuation(text: str) -> bool:
    return bool(text and text[-1] in JAPANESE_SOFT_PUNCTUATION)


def _block_text_length(block: SubtitleBlock) -> int:
    return sum(len(item.text) for item in block.utterances)
