"""Finalize: the deliverable subtitles, a styled ASS and a player SRT.

Three deterministic passes run here, after every agent pass, so nothing
upstream can undo them:

- Mixed-script name spacing. Latin-containing renderings agreed in the
  briefing (proper nouns, character names, glossary) plus the curated
  glossary's mixed Chinese-Latin names are rewritten to their canonical form,
  with exactly one half-width space against adjacent Han/kana.
- Netflix Traditional Chinese punctuation:
  - a spaced or punctuation-led slash, half- or full-width, marks a speaker
    change: in a one-line block with one such slash it becomes two `-` lines
    (`誰啊？/山內` → `-誰啊？` / `-山內`), otherwise one unspaced full-width
    slash, never a comma; an unspaced one between words (`1/2`) stays;
  - strip `，、；。` and surrounding whitespace at line edges (no terminal
    commas or periods);
  - normalize a leading speaker dash to a half-width `-` with no space;
  - collapse ellipsis runs (3+ `.`, `…`, `⋯`, mixed) into one `…`;
  - strip `，、；。` right before a closing `」`/`』` (`？！…` stay);
  - drop spaces hugging mid-line full-width `，、；。：！？`;
  - turn any remaining mid-line `。` into `，`.
- Over-wide lines: a line wider than the full canvas is broken into rows
  (`fit_text`), so the text itself carries every row drawn and the telop
  placement reads them from the SRT.

The ASS and the SRT carry the same text; the ASS uses `subtitles.ass`'s
dialogue header and style.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import TYPE_CHECKING

from grillmaster.core.fs import atomic_write_text
from grillmaster.core.srt import write_srt_file
from grillmaster.core.timecode import ass_time_from_ms, parse_timecode_ms
from grillmaster.subtitles.ass import (
    DIALOGUE_FONT_SIZE,
    DIALOGUE_HEADER,
    DIALOGUE_MARGINS,
    DIALOGUE_STYLE_NAME,
    LineBreaks,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from pathlib import Path

    from grillmaster.core.briefing import Briefing
    from grillmaster.core.srt import SrtBlock
    from grillmaster.glossary.fixed import FixedGlossary

_LINE_EDGE_PUNCT = re.compile(r"^[\s，、；。]+|[\s，、；。]+$")
_ELLIPSIS_RUN = re.compile(r"(?:\.{3,}|[…⋯])+")
_QUOTE_TAIL_PUNCT = re.compile(r"[\s，、；。]+(?=[」』])")
# Full-width punctuation carries no adjacent spaces. Refine writes two
# clauses on one line as "好帥。 很有型", and the "。"→"，" pass would otherwise
# leave "好帥， 很有型".
_FW_PUNCT = "，、；。：！？"
_FW_PUNCT_SPACE = re.compile(rf"[ \t　]*([{_FW_PUNCT}])[ \t　]*")
# Two-speaker dialogue uses an English hyphen with no space after it. Any
# leading hyphen/dash variant (incl. full-width "－", en/em dash, minus) plus
# surrounding spaces becomes "-". A leading "--" (interruption) keeps its
# second dash: only the first char is matched.
_SPEAKER_DASH = re.compile(r"^[ \t　]*[-‐-―−－][ \t　]*")  # noqa: RUF001 - dash variants on purpose
# Models split two speakers with a half- or full-width slash instead of a
# dash line ("解散了。/那當然", "誰啊？/ 山內"). A slash after sentence
# punctuation or a closing bracket, or a spaced one, is such a turn change.
_SEPARATOR_SLASH = re.compile(r"[ \t　]*[/／][ \t　]*")  # noqa: RUF001 - full-width slash on purpose
_SENTENCE_PUNCT = frozenset(f"{_FW_PUNCT}…?!.")
_CLOSING_BRACKETS = frozenset("」』）)》")
# Stands in for a turn-change slash until the block's shape is known.
_TURN = "\x1f"
_TURN_EDGE = re.compile(rf"[\s，、；。]*{_TURN}[\s，、；。]*")

# Han + kana letters that a Latin-containing name unit is separated from by
# one half-width space. CJK punctuation, the middle dot (U+30FB), the
# choonpu (U+30FC), full-width forms, ASCII punctuation and whitespace are
# excluded so the unit hugs punctuation and line edges.
_CJK_RE = re.compile(r"[ぁ-ゖァ-ヺ㐀-䶿一-鿿豈-﫿]")
_HAS_LATIN_RE = re.compile(r"[A-Za-z]")
_INNER_SPACE = re.compile(r"[ \t]+")


# --- name units -------------------------------------------------------------


def briefing_name_units(briefing: Briefing) -> list[str]:
    """Renderings the briefing agreed on: proper-noun targets, character
    `name_zh`, glossary targets. The spacer keeps only Latin-containing ones."""
    return [
        *(term.target for term in briefing.proper_nouns),
        *(character.name_zh for character in briefing.characters),
        *(term.target for term in briefing.glossary),
    ]


def curated_name_units(glossary: FixedGlossary) -> list[str]:
    """Curated renderings mixing a Han/kana char and a Latin letter
    (`水川Katamari`, `空前Meteor`).

    These are distinctive enough to space even when this episode's briefing
    missed them. Pure-Latin curated names (`Diane`, `THE SECOND`) are left to
    the briefing: across the whole catalog they would match by coincidence.
    """
    return [
        entry.zh
        for entry in glossary.entries()
        if _HAS_LATIN_RE.search(entry.zh) and _CJK_RE.search(entry.zh)
    ]


def name_units(briefing: Briefing, glossary: FixedGlossary) -> list[str]:
    return [*briefing_name_units(briefing), *curated_name_units(glossary)]


def _side_space(neighbor: str, *, had_space: bool) -> str:
    """Half-width space on one edge of a Latin name unit.

    Han/kana neighbor: one space. Line edge, punctuation or quote: none.
    Another word char (an adjacent romanized name, a digit): keep one space
    only if a separator existed. A whitespace neighbor belongs to the
    adjacent match: none.
    """
    if not neighbor or neighbor.isspace():
        return ""
    if _CJK_RE.match(neighbor):
        return " "
    if neighbor.isalnum():
        return " " if had_space else ""
    return ""


def build_name_spacer(units: Iterable[str]) -> Callable[[str], str]:
    """A per-line spacer rewriting each Latin-containing unit to its canonical
    form.

    Units match longest first (`Diane津田` before `Diane`). The matcher
    tolerates spaces or tabs an LLM inserted or removed inside a unit
    (`金屬 Bat`, `LongCoatDaddy`) but never other characters, so names with
    real text between them are never merged. Pure Han/kana names never
    qualify. No units means identity.
    """
    canonical = sorted(
        {unit.strip() for unit in units if unit.strip() and _HAS_LATIN_RE.search(unit)},
        key=len,
        reverse=True,
    )
    if not canonical:
        return lambda text: text

    def squash(text: str) -> str:
        return _INNER_SPACE.sub("", text)

    by_squashed = {squash(unit): unit for unit in canonical}
    alternation = "|".join(
        r"[ \t]*".join(re.escape(char) for char in squash(unit)) for unit in canonical
    )
    # The outer [ \t]* is consumed so wrong or doubled boundary spaces are
    # rewritten, not just supplemented.
    pattern = re.compile(rf"[ \t]*(?P<name>{alternation})[ \t]*")

    def space_line(line: str) -> str:
        def repl(match: re.Match[str]) -> str:
            whole = match.group(0)
            name = match.group("name")
            before = line[match.start() - 1] if match.start() > 0 else ""
            after = line[match.end()] if match.end() < len(line) else ""
            left = _side_space(before, had_space=whole != whole.lstrip(" \t"))
            right = _side_space(after, had_space=whole != whole.rstrip(" \t"))
            return f"{left}{by_squashed.get(squash(name), name)}{right}"

        return pattern.sub(repl, line)

    # Per physical line: a name is never spaced across a wrap.
    return lambda text: "\n".join(space_line(line) for line in text.split("\n"))


# --- punctuation ------------------------------------------------------------


def _split_turns(text: str) -> list[str]:
    """The lines of `text` with turn-change slashes resolved: two turns of
    one line each (`A／B`, or `A` and `／B` on two lines) become two `-`
    lines; more turns keep each inner slash as a full-width one, as a comma
    would merge the voices, and drop those at a line edge."""  # noqa: RUF002
    lines = [_mark_turns(line) for line in text.split("\n")]
    turns = re.split(rf"\s*{_TURN}\s*", "\n".join(lines).strip(f"{_TURN} \t　"))
    if len(turns) == 2 and not any("\n" in turn for turn in turns):  # noqa: PLR2004
        return [f"-{_SPEAKER_DASH.sub('', turn)}" for turn in turns]
    return [_TURN_EDGE.sub("／", line.strip(_TURN)) for line in lines]  # noqa: RUF001


def _mark_turns(line: str) -> str:
    """`line` with each turn-change slash as `_TURN`; one between words
    (`1/2`, `和平／剪刀手`, `AC / DC`) stays."""  # noqa: RUF002

    def repl(match: re.Match[str]) -> str:
        before = line[match.start() - 1] if match.start() > 0 else ""
        after = line[match.end()] if match.end() < len(line) else ""
        spaced = len(match.group(0)) > 1 or before in _CLOSING_BRACKETS
        if (
            not before
            or not after
            or before in _SENTENCE_PUNCT
            or (spaced and not (_is_ascii_word(before) and _is_ascii_word(after)))
        ):
            return _TURN
        return match.group(0)

    return _SEPARATOR_SLASH.sub(repl, line)


def _is_ascii_word(char: str) -> bool:
    return char.isascii() and char.isalnum()


def clean_line(line: str) -> str:
    line = _LINE_EDGE_PUNCT.sub("", line)
    line = _SPEAKER_DASH.sub("-", line)
    line = _ELLIPSIS_RUN.sub("…", line)
    line = _QUOTE_TAIL_PUNCT.sub("", line)
    line = _FW_PUNCT_SPACE.sub(r"\1", line)
    return line.replace("。", "，")


def clean_text(text: str) -> str:
    return "\n".join(clean_line(line) for line in _split_turns(text))


# --- over-wide lines --------------------------------------------------------

# The dialogue has room for ~33 characters a row, so a part may stray
# further from an even share than a telop's to end after a pause: at 20 %
# 37 of the 75 over-wide lines broke inside a phrase, at 35 % 19
# (2026-10-29; the owner may tune it).
DIALOGUE_BREAKS = LineBreaks(DIALOGUE_FONT_SIZE, uneven=0.35)


def fit_text(text: str) -> str:
    """Each line too wide for the full canvas broken into rows
    (`DIALOGUE_BREAKS`): libass breaks only at a space, so a CJK one ran off
    both edges. A row the break leaves ending in `，、；。` loses it, as any
    line does (only that rule: a row is not a new line to clean)."""
    return "\n".join(
        _LINE_EDGE_PUNCT.sub("", row)
        for row in DIALOGUE_BREAKS.broken(text.split("\n"), DIALOGUE_MARGINS.room)
    )


# --- output -----------------------------------------------------------------


def finalize_blocks(blocks: Iterable[SrtBlock], units: Iterable[str]) -> list[SrtBlock]:
    """Name spacing, punctuation cleanup, then over-wide lines broken, on
    every block's text."""
    space = build_name_spacer(units)
    return [
        replace(block, text=fit_text(clean_text(space(block.text)))) for block in blocks
    ]


def dialogue_line(block: SrtBlock) -> str:
    """One ASS `Dialogue` event for an already finalized block."""
    start_ms, end_ms = parse_timecode_ms(block.timecode)
    text = block.text.replace("\n", "\\N")
    return (
        f"Dialogue: 0,{ass_time_from_ms(start_ms)},{ass_time_from_ms(end_ms)},"
        f"{DIALOGUE_STYLE_NAME},,0,0,0,,{text}"
    )


def render_ass(blocks: Sequence[SrtBlock]) -> str:
    return DIALOGUE_HEADER + "\n".join(dialogue_line(block) for block in blocks) + "\n"


def write_finalized(
    blocks: Iterable[SrtBlock],
    *,
    units: Iterable[str],
    ass_path: Path,
    srt_path: Path,
) -> list[SrtBlock]:
    """Finalize `blocks` and write both deliverables; returns the final blocks."""
    final = finalize_blocks(blocks, units)
    atomic_write_text(ass_path, render_ass(final))
    write_srt_file(srt_path, final)
    return final
