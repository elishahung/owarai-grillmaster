"""The selector: one agent call keeps the cards worth translating and
translates them.

It runs once the dialogue subtitles are final and gets the offered cards
as a table (lines numbered, those repeating the speech marked), the
dialogue as Japanese beside the final Chinese subtitles (to recognise cards
that only repeat the speech, and to word the cards like the subtitles), and
the effective briefing; it may look at any card with `get_frames`. Its
answer lists only the kept cards, each with the lines its translation
covers (a name plate the rules joined to a caption that repeats the speech
is translated alone, beside the plate); Python checks every id names a
card, once, with a translation and valid lines, then writes
`TelopSubtitles`: the deliverable packaging burns in, readable and editable
by hand (delete an entry or change its `zh`, then `grill package`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import Field

from grillmaster.agents.errors import ValidationFailure
from grillmaster.agents.task import AgentTask, SchemaOutput
from grillmaster.core.id_coverage import id_coverage
from grillmaster.core.model_spec import Role
from grillmaster.core.models import StrictModel
from grillmaster.core.prompts import (
    frames_guidance,
    join_sections,
    load_prompt,
    render_program_instruction,
)
from grillmaster.telop.scan import Box, normalize

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from grillmaster.core.briefing import Briefing
    from grillmaster.core.srt import SrtBlock
    from grillmaster.core.tool_session import ToolSession
    from grillmaster.telop.scan import TelopCard

TASK_NAME = "telop"


class KeptTelop(StrictModel):
    """`lines`: the card's lines (numbered from 1) the translation covers;
    it is placed beside them."""

    id: int
    lines: list[int]
    zh: str


class TelopSelection(StrictModel):
    """The selector's answer: the kept cards only."""

    kept: list[KeptTelop]


class TelopSubtitle(StrictModel):
    """One translated card: on screen from `start` to `end` (seconds), `box`
    as fractions of the frame (for a future placement beside the card)."""

    id: int
    start: float
    end: float
    ja: str
    zh: str
    box: Box


class TelopSubtitles(StrictModel):
    """`.subs/telop.cht.json`, in time order."""

    items: list[TelopSubtitle] = Field(default_factory=list[TelopSubtitle])


@dataclass(frozen=True, slots=True)
class SelectInputs:
    """`transcript` is `ja.srt`, `subtitles` the final Chinese subtitles of
    the same blocks (`video.cht.srt`)."""

    cards: Sequence[TelopCard]
    transcript: Sequence[SrtBlock]
    subtitles: Sequence[SrtBlock]
    briefing: Briefing
    program_instruction: str


def _position(box: Box) -> str:
    x = (box[0] + box[2]) / 2
    y = (box[1] + box[3]) / 2
    row = "top" if y < 1 / 3 else "middle" if y < 2 / 3 else "bottom"
    column = "left" if x < 1 / 3 else "center" if x < 2 / 3 else "right"
    return f"{row}-{column}"


def render_cards(cards: Sequence[TelopCard]) -> str:
    """The card table; times in seconds, as `get_frames` takes them, lines
    numbered, `echoes_speech` the numbers of the lines that repeat the speech."""
    rows = [
        f"{card.id}\t{card.start:.1f}-{card.end:.1f}\t{_position(card.box)}\t"
        + ",".join(
            str(number)
            for number, line in enumerate(card.lines, 1)
            if line.echoes_speech
        )
        + "\t"
        + " / ".join(
            f"[{number}] {line.text}" for number, line in enumerate(card.lines, 1)
        )
        for card in cards
    ]
    return "id\tseconds\tposition\techoes_speech\ttext\n" + "\n".join(rows)


def _flat(text: str) -> str:
    return " / ".join(line.strip() for line in text.splitlines() if line.strip())


def render_dialogue(
    transcript: Sequence[SrtBlock], subtitles: Sequence[SrtBlock]
) -> str:
    """Each block's start, Japanese and final Chinese, paired by index."""
    zh = {block.index: block.text for block in subtitles}
    rows = [
        f"{block.time_range.start:.1f}\t{_flat(block.text)}\t"
        f"{_flat(zh.get(block.index, ''))}"
        for block in transcript
    ]
    return "seconds\tja\tzh\n" + "\n".join(rows)


def build_select_task(
    inputs: SelectInputs, *, session_dir: Path, tools: ToolSession
) -> AgentTask[TelopSelection]:
    prompt = join_sections(
        f"## Cards ({len(inputs.cards)})\n\n```tsv\n{render_cards(inputs.cards)}\n```",
        "## Dialogue (Japanese ASR beside the final Chinese subtitles)\n\n"
        f"```tsv\n{render_dialogue(inputs.transcript, inputs.subtitles)}\n```",
        f"## Briefing\n\n```json\n{inputs.briefing.render_for_prompt()}\n```",
        frames_guidance(),
    )
    return AgentTask(
        name=TASK_NAME,
        role=Role.UTILITY,
        instructions=join_sections(
            load_prompt(__package__, "select.md"),
            render_program_instruction(inputs.program_instruction),
        ),
        prompt=prompt,
        session_dir=session_dir,
        workdir=None,
        output=SchemaOutput(TelopSelection),
        tools=tools,
        validate=make_validator({card.id: len(card.lines) for card in inputs.cards}),
    )


def make_validator(line_counts: dict[int, int]) -> Callable[[TelopSelection], None]:
    """`ValidationFailure` for an id that names no card or repeats, for an
    empty translation, and for `lines` that are empty, repeat a line or name
    one the card does not have (`line_counts`: lines per card id)."""

    def validate(answer: TelopSelection) -> None:
        coverage = id_coverage(
            set(line_counts),
            ((kept.id, kept.zh.strip()) for kept in answer.kept),
            require_all=False,
        )
        problems = [
            *(f"id {i} is not a card in the table" for i in coverage.unknown),
            *(f"id {i} is listed more than once" for i in coverage.duplicate),
            *(f"id {i} has an empty zh" for i in coverage.empty),
        ]
        for kept in answer.kept:
            count = line_counts.get(kept.id)
            if count is None:
                continue
            if not kept.lines:
                problems.append(f"id {kept.id} lists no lines")
            elif len(set(kept.lines)) != len(kept.lines):
                problems.append(f"id {kept.id} lists a line more than once")
            elif not all(1 <= number <= count for number in kept.lines):
                problems.append(f"id {kept.id} has only lines 1-{count}")
        if problems:
            raise ValidationFailure.from_problems(
                "Fix these kept cards and return the whole list again:", problems
            )

    return validate


def to_subtitles(
    cards: Sequence[TelopCard], selection: TelopSelection
) -> TelopSubtitles:
    """The kept cards with their translations, in time order, each with the
    text and box of the lines it covers; a translation that reads exactly
    like those lines (a place name in kanji) tells the viewer nothing and is
    dropped."""
    by_id = {card.id: card for card in cards}
    items: list[TelopSubtitle] = []
    for kept in selection.kept:
        card = by_id[kept.id]
        covered = card.model_copy(
            update={"lines": [card.lines[number - 1] for number in sorted(kept.lines)]}
        )
        if normalize(kept.zh) == normalize(
            "".join(line.text for line in covered.lines)
        ):
            continue
        items.append(
            TelopSubtitle(
                id=card.id,
                start=card.start,
                end=card.end,
                ja=covered.text,
                zh=kept.zh.strip(),
                box=covered.box,
            )
        )
    return TelopSubtitles(items=sorted(items, key=lambda item: (item.start, item.id)))
