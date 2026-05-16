"""Convert ElevenLabs word-level ASR JSON into source SRT subtitles."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from services.srt import SrtBlock, format_timecode, serialize_srt


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


# ---------------------------------------------------------------------
# Source-SRT formatting parameters.
#
# These are intentionally hard-coded module constants rather than
# user-tunable settings — they're fine-tuned over time against real
# ASR output, not configuration knobs the pipeline user should touch.
# Tests exercise alternative values via the private
# `_convert_payload_with_options` entry point.
# ---------------------------------------------------------------------

MAX_CHARACTERS_PER_LINE = 24
MAX_SEGMENT_CHARS = 44
MAX_SEGMENT_DURATION_S = 0.0  # 0 disables duration-based splitting
SEGMENT_ON_SILENCE_LONGER_THAN_S = 0.7
MERGE_SPEAKER_TURNS_GAP_S = 0.05
MERGE_SAME_SPEAKER_GAP_S = 0.25
MERGE_OVERLAPPING_BLOCKS = True
MAX_OVERLAPPING_BLOCK_DURATION_S = 8.0
MAX_UTTERANCES_PER_BLOCK = 5
MAX_LINES_PER_BLOCK = 2
INLINE_SHORT_SAME_SPEAKER_UTTERANCES = True
MAX_INLINE_SHORT_UTTERANCE_CHARS = 8
MAX_ORPHAN_TAIL_CHARS = 8
MIN_SEGMENT_DURATION_S = 0.35
DRAG_FILLER_MIN_DURATION_S = 0.6
SUBTITLE_HOLD_AFTER_END_S = 0.5
MIN_INTER_SUBTITLE_GAP_S = 0.08
DIALOGUE_PREFIX = "-"
INCLUDE_SPEAKER_PREFIX_FOR_DIALOGUE = True
TEXT_JOIN_LANGUAGE = "ja"
IGNORED_WORD_TYPES: frozenset[str] = frozenset({"audio_event"})


# ---------------------------------------------------------------------
# Tuning notes — empirical rationale for non-obvious values above.
# Re-validate against representative ASR JSON if you change either.
# ---------------------------------------------------------------------
#
# MAX_SEGMENT_CHARS = 44  (was 48)
#   With max=48 (= 2*24 = exactly two lines), utterances can grow a few
#   chars past the cap when the segmenter absorbs a trailing punctuation
#   token (e.g. accumulator hits 47, next token is `。`, splitting before
#   `。` is forbidden so the period attaches and we end at 49). The wrap
#   then renders the over-cap utterance in 3 lines.
#   Test sweep across 3 ASR files (≈1900 blocks total) showed: 48 → 10
#   3-line blocks (test3 only); 44 → 4; 42 → 4 (no further gain). Cap=44
#   leaves enough head-room for trailing punctuation and eliminates 6 of
#   10 cases without splitting any clean 2-line utterances. Remaining 4
#   are utterances ≥49 chars with no internal punctuation, intrinsic to
#   rapid variety-show speech.
#
# MERGE_SPEAKER_TURNS_GAP_S = 0.05  (was 0.45)
#   Cross-speaker gap distribution across 308 transitions in 3 ASR files
#   is roughly flat between 0.01 and 0.10 with a density peak in
#   [0.05, 0.10) and another in [0.45, ∞). The original 0.45 swept up the
#   entire flat region plus part of the long tail, merging unrelated
#   narration into dialog blocks.
#   Qualitative review of marginal merges per band:
#     0.01→0.03  every sample is clear back-and-forth dialog (ideal)
#     0.03→0.05  Q&A and reactions (ideal)
#     0.05→0.08  mostly dialog reactions; mixing in narration adjacency
#     0.08→0.10  ~half are co-narration that shouldn't have merged
#   Response-cue ratio (B utterance starts with はい/いや/なるほど/etc.)
#   peaks at 0.05 (≈28%, vs 25-26% at 0.08-0.10). 0.05 captures rapid
#   turn-taking while excluding the narration-glue band.
#   0.01 is too aggressive — rejects ~25ms quick-fire dialog like
#   "だからそれもそういうこと → でもかっけ方".
#
# Both values can be overridden per-test via SrtFormatOptions; production
# uses the constants.
#
# SUBTITLE_HOLD_AFTER_END_S = 0.5
# MIN_INTER_SUBTITLE_GAP_S  = 0.08
#   ASR end times match the exact speech end, but a viewer's eye needs
#   a beat to finish reading after the talker stops. The Netflix Timed
#   Text Style Guide (Japanese) sets the minimum on-screen hold at
#   0.5 s; the BBC and EBU give similar guidance ("≥0.4 s after speech
#   ends" is the common practitioner rule). 0.5 is the comfortable
#   default; 0.3-0.4 would feel tighter for rapid variety-show banter
#   if needed.
#   The 0.08 s minimum gap to the next block (≈2 frames at 25 fps) is
#   the EBU/Netflix "≤2 frames OR ≥500 ms — nothing in between" rule:
#   the perceptual flicker zone is the awkward 80-500 ms window, so we
#   either butt blocks tightly (gap ≈ 80 ms) or leave a clear hold
#   (gap ≥ 500 ms when the next block is far away). Tight back-to-back
#   blocks (`next.start ≈ previous.end`) get no extension — the cap
#   sits below the existing end and the no-shrink rule keeps them as
#   the segmenter laid them out.
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class SrtFormatOptions:
    """Internal formatting controls.

    Defaults read from the module-level constants above so that the
    private testing entry point can override individual fields without
    drifting from production values."""

    max_characters_per_line: int = MAX_CHARACTERS_PER_LINE
    max_segment_chars: int = MAX_SEGMENT_CHARS
    max_segment_duration_s: float = MAX_SEGMENT_DURATION_S
    segment_on_silence_longer_than_s: float = SEGMENT_ON_SILENCE_LONGER_THAN_S
    merge_speaker_turns_gap_s: float = MERGE_SPEAKER_TURNS_GAP_S
    merge_same_speaker_gap_s: float = MERGE_SAME_SPEAKER_GAP_S
    merge_overlapping_blocks: bool = MERGE_OVERLAPPING_BLOCKS
    max_overlapping_block_duration_s: float = MAX_OVERLAPPING_BLOCK_DURATION_S
    max_utterances_per_block: int = MAX_UTTERANCES_PER_BLOCK
    max_lines_per_block: int = MAX_LINES_PER_BLOCK
    inline_short_same_speaker_utterances: bool = (
        INLINE_SHORT_SAME_SPEAKER_UTTERANCES
    )
    max_inline_short_utterance_chars: int = MAX_INLINE_SHORT_UTTERANCE_CHARS
    max_orphan_tail_chars: int = MAX_ORPHAN_TAIL_CHARS
    min_segment_duration_s: float = MIN_SEGMENT_DURATION_S
    drag_filler_min_duration_s: float = DRAG_FILLER_MIN_DURATION_S
    subtitle_hold_after_end_s: float = SUBTITLE_HOLD_AFTER_END_S
    min_inter_subtitle_gap_s: float = MIN_INTER_SUBTITLE_GAP_S
    split_on_punctuation: str = JAPANESE_HARD_PUNCTUATION
    soft_split_punctuation: str = JAPANESE_SOFT_PUNCTUATION
    dialogue_prefix: str = DIALOGUE_PREFIX
    include_speaker_prefix_for_dialogue: bool = (
        INCLUDE_SPEAKER_PREFIX_FOR_DIALOGUE
    )
    text_join_language: str = TEXT_JOIN_LANGUAGE
    ignored_word_types: frozenset[str] = IGNORED_WORD_TYPES


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


def convert_file(
    input_path: str | Path,
    output_path: str | Path,
) -> None:
    """Convert an ElevenLabs ASR JSON file to SRT under fixed parameters."""
    input_path = Path(input_path)
    output_path = Path(output_path)
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    srt = convert_payload_to_srt(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(srt, encoding="utf-8")
    logger.success(f"Converted ElevenLabs ASR JSON to SRT: {output_path}")


def convert_payload_to_srt(payload: dict[str, Any]) -> str:
    """Convert an ElevenLabs ASR payload to SRT under fixed parameters."""
    return _convert_payload_with_options(payload, SrtFormatOptions())


def _convert_payload_with_options(
    payload: dict[str, Any], options: SrtFormatOptions
) -> str:
    """Internal entry point that allows option overrides — for tests
    and fine-tuning experiments only. Production code calls
    `convert_payload_to_srt` / `convert_file`."""
    tokens = _extract_tokens(payload, options)
    if not tokens:
        raise ValueError("ElevenLabs ASR JSON does not contain timed words")

    utterances = _build_utterances(tokens, options)
    blocks = _merge_utterances_to_blocks(utterances, options)
    if options.merge_overlapping_blocks:
        blocks = _merge_overlapping_blocks(blocks, options)
    if options.inline_short_same_speaker_utterances:
        for block in blocks:
            if len(block.utterances) >= 2:
                block.utterances = _inline_same_speaker_utterances(
                    block.utterances, options
                )
    _resolve_block_overlaps(blocks, options)
    _extend_subtitle_hold_times(blocks, options)
    return _render_srt(blocks, options)


def _extract_tokens(
    payload: dict[str, Any], options: SrtFormatOptions
) -> list[WordToken]:
    word_items = _extract_word_items(payload)
    tokens: list[WordToken] = []
    for item in word_items:
        if not isinstance(item, dict):
            continue
        word_type = item.get("type")
        if word_type in options.ignored_word_types:
            continue
        text = str(item.get("text") or "")
        if not text:
            continue
        start = item.get("start")
        end = item.get("end")
        if not isinstance(start, (int, float)) or not isinstance(
            end, (int, float)
        ):
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


def _extract_word_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("words"), list):
        return payload["words"]

    transcripts = payload.get("transcripts")
    if isinstance(transcripts, list):
        words: list[dict[str, Any]] = []
        for transcript in transcripts:
            if isinstance(transcript, dict) and isinstance(
                transcript.get("words"), list
            ):
                words.extend(transcript["words"])
        return words

    return []


def _build_utterances(
    tokens: list[WordToken], options: SrtFormatOptions
) -> list[Utterance]:
    utterances: list[Utterance] = []
    current: list[WordToken] = []

    for index, token in enumerate(tokens):
        if not current:
            current.append(token)
            continue

        split_index = _choose_utterance_split_index(
            current, token, tokens, index, options
        )
        if split_index is not None:
            utterances.append(_tokens_to_utterance(current[:split_index], options))
            current = [*current[split_index:], token]
        elif _should_start_new_utterance(
            current, current[-1], token, tokens, index, options
        ):
            utterances.append(_tokens_to_utterance(current, options))
            current = [token]
        else:
            current.append(token)

    if current:
        utterances.append(_tokens_to_utterance(current, options))

    return [utterance for utterance in utterances if utterance.text]


def _should_start_new_utterance(
    current: list[WordToken],
    previous: WordToken,
    token: WordToken,
    tokens: list[WordToken],
    token_index: int,
    options: SrtFormatOptions,
) -> bool:
    if token.speaker_id != previous.speaker_id:
        return True
    unsafe_start = _is_unsafe_segment_start(token.text)
    if (
        token.start - previous.end > options.segment_on_silence_longer_than_s
        and not unsafe_start
        and not _is_short_soft_fragment(current, options)
        and not _would_create_short_orphan_tail(tokens, token_index, options)
        and not _is_dragged_single_kana(current, options)
    ):
        return True

    text = _join_token_texts(current, options)
    if _ends_with_split_punctuation(previous.text, options):
        return True
    if (
        _ends_with_soft_split_punctuation(previous.text, options)
        and len(text) >= options.max_characters_per_line
    ):
        return True
    return False


def _choose_utterance_split_index(
    current: list[WordToken],
    token: WordToken,
    tokens: list[WordToken],
    token_index: int,
    options: SrtFormatOptions,
) -> int | None:
    if token.speaker_id != current[-1].speaker_id:
        return None

    unsafe_start = _is_unsafe_segment_start(token.text)
    if unsafe_start or _would_create_short_orphan_tail(tokens, token_index, options):
        return None

    prospective_text = _join_token_texts([*current, token], options)
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

    split_index = _find_best_utterance_split_index(current, options)
    return split_index if split_index is not None else len(current)


def _find_best_utterance_split_index(
    tokens: list[WordToken], options: SrtFormatOptions
) -> int | None:
    # Walk every index from the end so that a punctuation at the
    # very last token is also considered — splitting at len(tokens)
    # emits the whole accumulated phrase and starts the next
    # utterance fresh with the incoming token.
    for index in range(len(tokens) - 1, -1, -1):
        token = tokens[index]
        if not (
            _ends_with_split_punctuation(token.text, options)
            or _ends_with_soft_split_punctuation(token.text, options)
        ):
            continue

        split_index = index + 1
        if _join_token_texts(tokens[:split_index], options):
            return split_index
    return None


def _is_short_soft_fragment(
    tokens: list[WordToken], options: SrtFormatOptions
) -> bool:
    if not tokens or not _ends_with_soft_split_punctuation(tokens[-1].text, options):
        return False
    text = _join_token_texts(tokens, options)
    return len(text) <= options.max_orphan_tail_chars


def _is_dragged_single_kana(
    current: list[WordToken], options: SrtFormatOptions
) -> bool:
    """Current is a single drawn-out kana (e.g. `さ`, `そ`, `あ` held
    for ≥ DRAG_FILLER_MIN_DURATION_S). Splitting would strand it as
    its own utterance; instead it should attach to the next phrase."""
    if len(current) != 1:
        return False
    text = current[0].text.strip()
    if len(text) != 1 or text in NO_SPACE_BEFORE:
        return False
    duration = current[0].end - current[0].start
    return duration >= options.drag_filler_min_duration_s


def _would_create_short_orphan_tail(
    tokens: list[WordToken], start_index: int, options: SrtFormatOptions
) -> bool:
    if start_index >= len(tokens) or options.max_orphan_tail_chars <= 0:
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
        if raw_len > options.max_orphan_tail_chars:
            return False
        if _ends_with_split_punctuation(token.text, options):
            break

    if not tail or not _ends_with_split_punctuation(tail[-1].text, options):
        return False
    text = _join_token_texts(tail, options)
    return len(text) <= options.max_orphan_tail_chars


def _tokens_to_utterance(
    tokens: list[WordToken], options: SrtFormatOptions
) -> Utterance:
    start = tokens[0].start
    end = max(tokens[-1].end, start + options.min_segment_duration_s)
    return Utterance(
        speaker_id=tokens[0].speaker_id,
        start=start,
        end=end,
        text=_join_token_texts(tokens, options).strip(),
    )


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
            same_speaker = (
                block.utterances[-1].speaker_id == utterance.speaker_id
            )
            prev_ends_hard = _ends_with_split_punctuation(
                block.utterances[-1].text, options
            )
            if same_speaker and not prev_ends_hard:
                block.utterances[-1].text = _join_text_parts(
                    block.utterances[-1].text,
                    utterance.text,
                    options,
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
            and merged_duration <= options.max_overlapping_block_duration_s
            and len(previous.utterances) + len(block.utterances)
            <= options.max_utterances_per_block
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


def _resolve_block_overlaps(
    blocks: list[SubtitleBlock], options: SrtFormatOptions
) -> None:
    for index in range(1, len(blocks)):
        previous = blocks[index - 1]
        current = blocks[index]
        if previous.end <= current.start:
            continue

        if current.start - previous.start >= options.min_segment_duration_s:
            previous.end = current.start
            continue

        current.start = previous.end
        if current.end <= current.start:
            current.end = current.start + options.min_segment_duration_s


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
        if new_end > block.end:
            block.end = new_end


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
    if not (
        _is_short_inline_utterance(left, options)
        and _is_short_inline_utterance(right, options)
    ):
        return False
    gap = max(0.0, right.start - left.end)
    if gap > options.merge_same_speaker_gap_s:
        return False
    combined_text = f"{left.text} {right.text}"
    return len(combined_text) <= options.max_characters_per_line


def _is_short_inline_utterance(
    utterance: Utterance, options: SrtFormatOptions
) -> bool:
    text = utterance.text.strip()
    return (
        bool(text)
        and len(text) <= options.max_inline_short_utterance_chars
        and _ends_with_split_punctuation(text, options)
    )


def _can_merge_into_block(
    block: SubtitleBlock, utterance: Utterance, options: SrtFormatOptions
) -> bool:
    gap = utterance.start - block.end
    same_speaker = block.utterances[-1].speaker_id == utterance.speaker_id
    if same_speaker and _ends_with_split_punctuation(
        block.utterances[-1].text, options
    ):
        # Two complete same-speaker sentences normally never share a
        # block. Exception: both sides are short and inline-eligible —
        # allow the merge so `_inline_same_speaker_utterances` can join
        # them with a space (e.g. 「いいんすか？ それ。」).
        if not (
            options.inline_short_same_speaker_utterances
            and _can_inline_same_speaker_utterance(
                block.utterances[-1], utterance, options
            )
        ):
            return False
    max_gap = (
        options.merge_same_speaker_gap_s
        if same_speaker
        else options.merge_speaker_turns_gap_s
    )
    if gap < 0:
        gap = 0
    if gap > max_gap:
        return False
    if len(block.utterances) + 1 > options.max_utterances_per_block:
        return False
    candidate_utterances = (
        _inline_same_speaker_utterances(
            [*block.utterances, utterance], options
        )
        if options.inline_short_same_speaker_utterances
        else [*block.utterances, utterance]
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
    if _block_text_length(block) + len(utterance.text) > options.max_segment_chars:
        return False
    return True


def _render_srt(
    blocks: list[SubtitleBlock], options: SrtFormatOptions
) -> str:
    srt_blocks: list[SrtBlock] = []
    for index, block in enumerate(blocks, start=1):
        text_lines = _render_block_text(block, options)
        srt_blocks.append(
            SrtBlock(
                index=index,
                timecode=(
                    f"{format_timecode(block.start)} --> "
                    f"{format_timecode(block.end)}"
                ),
                text="\n".join(text_lines),
            )
        )
    return serialize_srt(srt_blocks)


def _render_block_text(
    block: SubtitleBlock, options: SrtFormatOptions
) -> list[str]:
    use_dialogue = (
        options.include_speaker_prefix_for_dialogue
        and len({item.speaker_id for item in block.utterances}) > 1
    )
    rendered: list[str] = []
    for utterance in block.utterances:
        text = utterance.text.strip()
        if not text:
            continue
        for line in _wrap_text(
            text, options, max_lines=options.max_lines_per_block
        ):
            if use_dialogue:
                rendered.append(f"{options.dialogue_prefix}{line}")
            else:
                rendered.append(line)
    return rendered


def _rendered_line_count(
    utterances: list[Utterance], options: SrtFormatOptions
) -> int:
    return sum(
        len(_wrap_text(utterance.text.strip(), options))
        for utterance in utterances
        if utterance.text.strip()
    )


def _wrap_text(
    text: str, options: SrtFormatOptions, *, max_lines: int | None = None
) -> list[str]:
    max_chars = options.max_characters_per_line
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]

    lines: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        split_at = _find_wrap_index(remaining, options)
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
            score == best_score
            and abs(i - target) < abs(best_index - target)
        ):
            best_score = score
            best_index = i

    head = text[:best_index].strip()
    tail = text[best_index:].strip()
    if not head or not tail:
        return [text]
    if max_lines == 2:
        return [head, tail]
    return [head, *_balanced_wrap(tail, max_lines - 1)]


def _find_wrap_index(text: str, options: SrtFormatOptions) -> int:
    max_chars = options.max_characters_per_line
    n = len(text)
    hi = min(max_chars, n - 1)
    # When the utterance fits in two lines (n <= 2 * max_chars) keep
    # `lo = n - max_chars` so the second line also stays within the
    # cap. When it doesn't fit, line 2+ will wrap recursively, so we
    # only require a non-empty line 1 — letting the scorer pick a
    # safe break instead of greedy-cutting mid-word at max_chars.
    if n <= 2 * max_chars:
        lo = max(1, n - max_chars)
    else:
        lo = 1
    if lo > hi:
        return max_chars

    midpoint = n / 2
    best_index = max_chars
    best_score = float("-inf")
    for i in range(lo, hi + 1):
        score = _score_wrap_break(text, i, midpoint)
        if score > best_score or (
            score == best_score
            and abs(i - midpoint) < abs(best_index - midpoint)
        ):
            best_score = score
            best_index = i
    return best_index


def _score_wrap_break(text: str, i: int, midpoint: float) -> float:
    line1 = text[:i]
    line2 = text[i:]
    score = 0.0

    last = line1[-1]
    ends_clause = (
        last in JAPANESE_HARD_PUNCTUATION or last in JAPANESE_SOFT_PUNCTUATION
    )

    # Unsafe-start penalty only when line 1 did not already terminate the
    # clause; otherwise breaking before a particle-like char is fine
    # (e.g. "...、|はたまた..." — `は` here is part of an adverb, not a
    # topic particle).
    if not ends_clause and _line_wrap_unsafe_start(line2):
        score -= 50.0

    shorter = min(len(line1), len(line2))
    if shorter <= 3:
        score -= 30.0
    elif shorter <= 6:
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
    return bool(re.match(r"[A-Za-z0-9]", ch))


def _join_token_texts(
    tokens: list[WordToken], options: SrtFormatOptions
) -> str:
    text = ""
    for token in tokens:
        text = _join_text_parts(text, token.text, options)
    return _normalize_spacing(text)


def _join_text_parts(
    left: str, right: str, options: SrtFormatOptions
) -> str:
    if not left:
        return right
    if not right:
        return left
    if options.text_join_language == "ja":
        if right[0] in NO_SPACE_BEFORE or left[-1] in NO_SPACE_AFTER:
            return left + right
        if _needs_ascii_space(left[-1], right[0]):
            return left + " " + right
        return left + right
    return left + " " + right


def _normalize_spacing(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([。、，,.！？?!：:；;）)\]」』】》〉])", r"\1", text)
    text = re.sub(r"([（(\[「『【《〈])\s+", r"\1", text)
    return text.strip()


def _needs_ascii_space(left: str, right: str) -> bool:
    return bool(re.match(r"[A-Za-z0-9]", left) and re.match(r"[A-Za-z0-9]", right))


def _is_unsafe_segment_start(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    return (
        stripped[0] in NO_SPACE_BEFORE
        or stripped[0] in JAPANESE_BOUND_KANA
        or stripped in JAPANESE_UNSAFE_SEGMENT_STARTS
    )


def _ends_with_split_punctuation(
    text: str, options: SrtFormatOptions
) -> bool:
    return bool(text and text[-1] in options.split_on_punctuation)


def _ends_with_soft_split_punctuation(
    text: str, options: SrtFormatOptions
) -> bool:
    return bool(text and text[-1] in options.soft_split_punctuation)


def _block_text_length(block: SubtitleBlock) -> int:
    return sum(len(item.text) for item in block.utterances)


