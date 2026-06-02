"""Convert translated SRT subtitles to styled ASS format.

Applies Traditional Chinese subtitle punctuation rules aligned with the
Netflix TC style guide:

- Strip leading/trailing ``，``/``、``/``；``/``。`` plus surrounding
  whitespace from each line (Netflix forbids terminal commas/periods at
  line endings).
- Collapse any run of ellipsis characters — 3+ half-width ``.``, one or
  more full-width ``…`` (U+2026) or ``⋯`` (U+22EF), or a mixed
  sequence — into a single ``…``.
- Strip ``[\\s，、；。]+`` immediately before a closing dialogue quote
  ``」`` or ``』`` (same rule as line edges, applied to the dialogue's
  inner end). ``？``/``！``/``…`` before the quote are preserved.
- Convert any remaining (mid-line) ``。`` to ``，`` for smoother visual
  flow — bare ``。`` mid-subtitle reads awkwardly.
- Preserve mid-sentence ``，``/``、``/``；`` and all other punctuation
  (``？``/``！``/``「」``/``『』``/``（）``/``《》``/``：``).
"""

import json
import re
from pathlib import Path
from typing import Callable, Iterable

from loguru import logger

from services.fixed_glossary import load_fixed_glossary
from services.srt import SrtBlock, parse_srt, serialize_srt

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,源泉圓體月 M,64,&H00FDFDFD,&H000000FF,&H00000000,&H7D000000,0,0,0,0,100,100,0,0,1,6,2,2,10,10,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

_LINE_EDGE_PUNCT = re.compile(r"^[\s，、；。]+|[\s，、；。]+$")
_ELLIPSIS_RUN = re.compile(r"(?:\.{3,}|[…⋯])+")
_QUOTE_TAIL_PUNCT = re.compile(r"[\s，、；。]+(?=[」』])")
# Netflix TC: full-width punctuation carries no adjacent spaces. Strip
# whitespace hugging a mid-line ，、；。：！？ — e.g. the refine LLM writes
# two clauses on one line as "好帥。 很有型", and the "。"→"，" pass would
# otherwise leave "好帥， 很有型".
_FW_PUNCT_SPACE = re.compile(r"[ \t　]*([，、；。：！？])[ \t　]*")
# Netflix Traditional Chinese (Taiwan) TTSG: two-speaker dialogue uses an
# English hyphen with NO space after it. Normalize a leading speaker dash —
# any hyphen/dash variant incl. full-width "－" (U+FF0D), en/em dash, minus —
# plus surrounding spaces, to a single half-width "-". A leading "--"
# (interruption) keeps its second dash: only the first char is matched.
_SPEAKER_DASH = re.compile(
    r"^[ \t　]*[-‐-―−－][ \t　]*"
)
_SRT_TIMECODE = re.compile(
    r"^\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*$"
)

# Han + kana letters that must be separated from a Latin-containing name unit
# by one half-width space. CJK punctuation, the middle dot (U+30FB), the
# choonpu (U+30FC), full-width forms, ASCII punctuation and whitespace are
# deliberately excluded so the unit hugs punctuation/line edges.
_CJK_RE = re.compile(
    r"[ぁ-ゖァ-ヺ㐀-䶿一-鿿豈-﫿]"
)
_HAS_LATIN_RE = re.compile(r"[A-Za-z]")


def _side_space(neighbor: str, had_ws: bool) -> str:
    """Half-width space policy on one edge of a Latin name unit.

    Han/kana neighbor → exactly one space; line edge or punctuation/quote →
    none; another word char (an adjacent romanized name, a digit) → keep one
    space iff a separator already existed (never delete it, never fabricate);
    a whitespace neighbor is a separator owned by the adjacent match → none.
    """
    if neighbor == "":
        return ""
    if _CJK_RE.match(neighbor):
        return " "
    if neighbor.isspace():
        return ""
    if neighbor.isalnum():
        return " " if had_ws else ""
    return ""


def _load_latin_name_units(
    pre_pass_path: str | Path | None,
) -> list[str]:
    """Collect agreed proper-noun renderings from a pre_pass.json file.

    Missing/garbled file → [] (the spacing pass becomes a no-op). Only the
    target renderings are taken; the spacer filters to Latin-containing units.
    """
    if pre_pass_path is None:
        return []
    path = Path(pre_pass_path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    units: list[str] = []
    proper_nouns = data.get("proper_nouns")
    if isinstance(proper_nouns, dict):
        units.extend(v for v in proper_nouns.values() if isinstance(v, str))
    characters = data.get("characters")
    if isinstance(characters, list):
        units.extend(
            c["name_zh"]
            for c in characters
            if isinstance(c, dict) and isinstance(c.get("name_zh"), str)
        )
    glossary = data.get("glossary")
    if isinstance(glossary, dict):
        units.extend(v for v in glossary.values() if isinstance(v, str))
    return units


def _curated_name_units() -> list[str]:
    """Mixed Chinese-Latin zh renderings from the bundled curated glossary.

    Only names containing BOTH a Han/kana char AND a Latin letter are
    force-added (e.g. `水川Katamari`, `空前Meteor`). These are highly
    distinctive, so backfilling them is safe even when this episode's
    pre_pass missed them (pre_pass often only mentions a name inside a
    segment summary). Pure-Latin curated names (e.g. `Diane`, `THE SECOND`)
    are deliberately NOT force-added from the whole catalog — they are
    episode-vetted via pre_pass when relevant and far more prone to
    coincidental substring matches. Pure-Han names never qualify (no Latin).
    """
    glossary = load_fixed_glossary()
    units: list[str] = []
    for unit in glossary.talents:
        units.extend(zh for _, zh in unit.entries())
    units.extend(zh for _, zh in glossary.others)
    return [
        zh
        for zh in units
        if _HAS_LATIN_RE.search(zh) and _CJK_RE.search(zh)
    ]


def _build_latin_name_spacer(
    latin_name_units: Iterable[str],
) -> Callable[[str], str]:
    """Compile a per-line spacer for the given Latin-containing name units.

    Each unit is one indivisible token rewritten to its clean canonical form:
    exactly one half-width space against adjacent Han/kana, none inside the
    unit or against punctuation/line edges. Pure Han/kana names never enter
    `units` (no Latin letter) so they are left untouched.

    The matcher also tolerates `[ \\t]` that an upstream LLM may have wrongly
    inserted *inside* a unit (e.g. `金屬 Bat`, `Imadei 醬`, `Long  Coat
    Daddy`, or even fully de-spaced `LongCoatDaddy`) and rewrites every hit
    back to the canonical form. Only whitespace is tolerated between unit
    characters — never other characters — so distinct names with real text
    between them are never merged. Returns identity when there are no units.
    """
    units = sorted(
        {
            u.strip()
            for u in latin_name_units
            if u and u.strip() and _HAS_LATIN_RE.search(u)
        },
        key=len,
        reverse=True,  # longest-first so `Diane津田` wins over `Diane`
    )
    if not units:
        return lambda text: text

    def _squash(s: str) -> str:
        return re.sub(r"[ \t]+", "", s)

    # Whitespace-free form → canonical rendering, so any mangled-whitespace
    # hit (split, extra, or removed spaces) is rewritten to the clean unit.
    canonical_by_squashed = {_squash(u): u for u in units}

    def _flexible(unit: str) -> str:
        # Optional [ \t] between every non-space character of the unit.
        return r"[ \t]*".join(re.escape(c) for c in _squash(unit))

    alternation = "|".join(_flexible(u) for u in units)
    # Outer [ \t]* is consumed so pre-existing wrong/duplicated boundary
    # spaces are rewritten, not just supplemented. The separator between two
    # adjacent romanized names (e.g. group + member `Two Tribe Takanori`) is
    # preserved by _side_space, not eaten.
    pattern = re.compile(rf"[ \t]*(?P<name>{alternation})[ \t]*")

    def space_line(line: str) -> str:
        def repl(match: re.Match) -> str:
            group = match.group(0)
            name = match.group("name")
            canonical = canonical_by_squashed.get(_squash(name), name)
            had_left = group != group.lstrip(" \t")
            had_right = group != group.rstrip(" \t")
            before = line[match.start() - 1] if match.start() > 0 else ""
            after = line[match.end()] if match.end() < len(line) else ""
            left = _side_space(before, had_left)
            right = _side_space(after, had_right)
            return f"{left}{canonical}{right}"

        return pattern.sub(repl, line)

    def space_text(text: str) -> str:
        # Per physical line: "line start/end" is the subtitle line edge, and a
        # name is never spaced across a wrap.
        return "\n".join(space_line(line) for line in text.split("\n"))

    return space_text


def _clean_line(line: str) -> str:
    line = _LINE_EDGE_PUNCT.sub("", line)
    line = _SPEAKER_DASH.sub("-", line)
    line = _ELLIPSIS_RUN.sub("…", line)
    line = _QUOTE_TAIL_PUNCT.sub("", line)
    line = _FW_PUNCT_SPACE.sub(r"\1", line)
    return line.replace("。", "，")


def _clean_text(text: str) -> str:
    return "\n".join(_clean_line(line) for line in text.split("\n"))


def _format_ass_time(h: str, m: str, s: str, ms: str) -> str:
    # ASS uses centiseconds with single-digit hour: H:MM:SS.cc.
    # Aegisub truncates the millisecond → centisecond conversion.
    return f"{int(h)}:{m}:{s}.{int(ms) // 10:02d}"


def _srt_timecode_to_ass(srt_timecode: str) -> tuple[str, str]:
    match = _SRT_TIMECODE.match(srt_timecode)
    if not match:
        raise ValueError(f"Invalid SRT timecode: {srt_timecode!r}")
    sh, sm, ss, sms, eh, em, es, ems = match.groups()
    return _format_ass_time(sh, sm, ss, sms), _format_ass_time(eh, em, es, ems)


def _block_to_dialogue(block: SrtBlock) -> str:
    start, end = _srt_timecode_to_ass(block.timecode)
    text = _clean_text(block.text).replace("\n", "\\N")
    return f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}"


def _render(blocks: Iterable[SrtBlock]) -> str:
    dialogue_lines = [_block_to_dialogue(b) for b in blocks]
    return ASS_HEADER + "\n".join(dialogue_lines) + "\n"


def finalize_and_export(
    input_path: str | Path,
    finalized_ass_path: str | Path,
    finalized_srt_path: str | Path | None = None,
    pre_pass_path: str | Path | None = None,
) -> None:
    """Read an SRT file, clean Chinese punctuation, and write a styled ASS file.

    When ``finalized_srt_path`` is provided, also writes a player-friendly SRT
    with the same punctuation cleanup applied to each block's text. The SRT
    output is intended for devices that don't support ASS.

    When ``pre_pass_path`` is provided, Latin/mixed proper-noun renderings from
    that pre_pass.json get deterministic Netflix-style spacing here — the last
    text stage, after any LLM refinement — so the spacing cannot be undone by
    the chunk translator or the refine pass.
    """
    input_path = Path(input_path)
    finalized_ass_path = Path(finalized_ass_path)

    srt_text = input_path.read_text(encoding="utf-8")
    blocks = parse_srt(srt_text)
    space_latin_names = _build_latin_name_spacer(
        _load_latin_name_units(pre_pass_path) + _curated_name_units()
    )
    blocks = [
        block.model_copy(update={"text": space_latin_names(block.text)})
        for block in blocks
    ]
    ass_text = _render(blocks)

    finalized_ass_path.parent.mkdir(parents=True, exist_ok=True)
    finalized_ass_path.write_text(ass_text, encoding="utf-8")
    logger.success(f"Converted SRT to ASS: {finalized_ass_path}")

    if finalized_srt_path is not None:
        srt_out = Path(finalized_srt_path)
        cleaned_blocks = [
            b.model_copy(update={"text": _clean_text(b.text)}) for b in blocks
        ]
        srt_out.parent.mkdir(parents=True, exist_ok=True)
        srt_out.write_text(serialize_srt(cleaned_blocks), encoding="utf-8")
        logger.success(f"Wrote finalized SRT: {srt_out}")
