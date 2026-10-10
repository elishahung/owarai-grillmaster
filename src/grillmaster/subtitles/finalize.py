"""Finalize: the deliverable subtitles, a styled ASS and a player SRT.

Two deterministic passes run here, after every agent pass, so nothing
upstream can undo them:

- Mixed-script name spacing. Latin-containing renderings agreed in the
  briefing (proper nouns, character names, glossary) plus the curated
  glossary's mixed Chinese-Latin names are rewritten to their canonical form,
  with exactly one half-width space against adjacent Han/kana.
- Netflix Traditional Chinese punctuation:
  - strip `，、；。` and surrounding whitespace at line edges (no terminal
    commas or periods);
  - normalize a leading speaker dash to a half-width `-` with no space;
  - collapse ellipsis runs (3+ `.`, `…`, `⋯`, mixed) into one `…`;
  - strip `，、；。` right before a closing `」`/`』` (`？！…` stay);
  - drop spaces hugging mid-line full-width `，、；。：！？`;
  - turn any remaining mid-line `。` into `，`.

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
from grillmaster.subtitles.ass import DIALOGUE_HEADER, DIALOGUE_STYLE_NAME

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
_FW_PUNCT_SPACE = re.compile(r"[ \t　]*([，、；。：！？])[ \t　]*")
# Two-speaker dialogue uses an English hyphen with no space after it. Any
# leading hyphen/dash variant (incl. full-width "－", en/em dash, minus) plus
# surrounding spaces becomes "-". A leading "--" (interruption) keeps its
# second dash: only the first char is matched.
_SPEAKER_DASH = re.compile(r"^[ \t　]*[-‐-―−－][ \t　]*")  # noqa: RUF001

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


def clean_line(line: str) -> str:
    line = _LINE_EDGE_PUNCT.sub("", line)
    line = _SPEAKER_DASH.sub("-", line)
    line = _ELLIPSIS_RUN.sub("…", line)
    line = _QUOTE_TAIL_PUNCT.sub("", line)
    line = _FW_PUNCT_SPACE.sub(r"\1", line)
    return line.replace("。", "，")


def clean_text(text: str) -> str:
    return "\n".join(clean_line(line) for line in text.split("\n"))


# --- output -----------------------------------------------------------------


def finalize_blocks(blocks: Iterable[SrtBlock], units: Iterable[str]) -> list[SrtBlock]:
    """Name spacing, then punctuation cleanup, on every block's text."""
    space = build_name_spacer(units)
    return [replace(block, text=clean_text(space(block.text))) for block in blocks]


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
