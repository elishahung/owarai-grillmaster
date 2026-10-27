"""The selector: one agent call keeps the cards worth translating and
translates them.

It runs once the dialogue subtitles are final and gets the offered cards
as a table (speech-like ones marked), the dialogue as Japanese beside the
final Chinese subtitles (to recognise cards that only repeat the speech,
and to word the cards like the subtitles), and the effective briefing; it
may look at any card with `get_frames`. Its answer lists only the kept
cards; Python checks every id names a card, once, with a translation, then
writes `TelopSubtitles`: the deliverable packaging burns in, readable and
editable by hand (delete an entry or change its `zh`, then `grill
package`).
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
from grillmaster.telop.scan import Box, CardKind, normalize

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
    """The card table; times in seconds, as `get_frames` takes them."""
    rows = [
        f"{card.id}\t{card.start:.1f}-{card.end:.1f}\t{_position(card.box)}\t"
        f"{'yes' if card.kind is CardKind.SPEECH else ''}\t{card.text}"
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
    """The kept cards with their translations, in time order; a translation
    that reads exactly like its card (a place name in kanji) tells the viewer
    nothing and is dropped."""
    by_id = {card.id: card for card in cards}
    items: list[TelopSubtitle] = []
    for kept in selection.kept:
        card = by_id[kept.id]
        if normalize(kept.zh) == normalize("".join(line.text for line in card.lines)):
            continue
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
