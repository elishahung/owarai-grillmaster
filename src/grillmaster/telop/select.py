"""The selector: one agent call keeps the cards worth translating and
translates them.

It gets the pre-filtered cards as a table, the Japanese transcript (to
recognise cards that only repeat the speech) and the briefing (names and
terms shared with the dialogue subtitles), and may look at any card with
`get_frames`. Its answer lists only the kept cards; Python checks every id
names a card, once, with a translation, then writes `TelopSubtitles`: the
deliverable packaging burns in, readable and editable by hand (delete an
entry or change its `zh`, then `grill package`).
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
from grillmaster.telop.scan import Box

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from grillmaster.core.briefing import Briefing
    from grillmaster.core.srt import SrtBlock
    from grillmaster.core.tool_session import ToolSession
    from grillmaster.telop.scan import TelopCard

TASK_NAME = "telop"


class KeptTelop(StrictModel):
    id: int
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
    cards: Sequence[TelopCard]
    transcript: Sequence[SrtBlock]
    briefing: Briefing
    program_instruction: str


def _position(box: Box) -> str:
    x = (box[0] + box[2]) / 2
    y = (box[1] + box[3]) / 2
    row = "top" if y < 1 / 3 else "middle" if y < 2 / 3 else "bottom"
    column = "left" if x < 1 / 3 else "center" if x < 2 / 3 else "right"
    return f"{row}-{column}"


def render_cards(cards: Sequence[TelopCard]) -> str:
    """The card table; times in seconds, as `get_frames` takes them."""
    rows = [
        f"{card.id}\t{card.start:.1f}-{card.end:.1f}\t"
        f"{_position(card.box)}\t{card.text}"
        for card in cards
    ]
    return "id\tseconds\tposition\ttext\n" + "\n".join(rows)


def render_transcript(blocks: Sequence[SrtBlock]) -> str:
    return "\n".join(
        f"[{block.time_range.start:.1f}] {' '.join(block.text.splitlines())}"
        for block in blocks
    )


def build_select_task(
    inputs: SelectInputs, *, session_dir: Path, tools: ToolSession
) -> AgentTask[TelopSelection]:
    prompt = join_sections(
        f"## Cards ({len(inputs.cards)})\n\n```tsv\n{render_cards(inputs.cards)}\n```",
        f"## Japanese transcript (ASR)\n\n{render_transcript(inputs.transcript)}",
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
        validate=make_validator({card.id for card in inputs.cards}),
    )


def make_validator(ids: set[int]) -> Callable[[TelopSelection], None]:
    """`ValidationFailure` for an id that names no card or repeats, and for
    an empty translation."""

    def validate(answer: TelopSelection) -> None:
        coverage = id_coverage(
            ids,
            ((kept.id, kept.zh.strip()) for kept in answer.kept),
            require_all=False,
        )
        problems = [
            *(f"id {i} is not a card in the table" for i in coverage.unknown),
            *(f"id {i} is listed more than once" for i in coverage.duplicate),
            *(f"id {i} has an empty zh" for i in coverage.empty),
        ]
        if problems:
            raise ValidationFailure.from_problems(
                "Fix these kept cards and return the whole list again:", problems
            )

    return validate


def to_subtitles(
    cards: Sequence[TelopCard], selection: TelopSelection
) -> TelopSubtitles:
    """The kept cards with their translations, in time order."""
    by_id = {card.id: card for card in cards}
    items: list[TelopSubtitle] = []
    for kept in selection.kept:
        card = by_id[kept.id]
        items.append(
            TelopSubtitle(
                id=card.id,
                start=card.start,
                end=card.end,
                ja=card.text,
                zh=kept.zh.strip(),
                box=card.box,
            )
        )
    return TelopSubtitles(items=sorted(items, key=lambda item: (item.start, item.id)))
