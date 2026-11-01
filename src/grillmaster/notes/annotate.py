"""The notes agent: one call decides what a Taiwanese viewer is missing and
writes a short note for each.

It runs once the dialogue subtitles and the translated cards are final,
and gets every block as Japanese beside the final Chinese, the translated
cards (what the viewer already reads on screen), the on-screen text table
(evidence), the effective briefing and the earlier passes' reports, whose
web findings it may reuse; it searches the web itself and may look at
frames. Each note is anchored to a block and quotes the term it explains
from that block; Python checks the anchors, that it fits the layer's two
rows and the evidence, then times the notes one after another: `Notes`,
the deliverable, readable and editable by hand.

A probe runs the same rules without any research and answers which
candidates it would note and why: a cheap way to tune the rules against
kept episodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import Field

from grillmaster.agents.adapters.base import Capability
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
from grillmaster.notes.render import MAX_ROWS, rows
from grillmaster.telop.select import flat

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from datetime import date
    from pathlib import Path

    from grillmaster.core.briefing import Briefing
    from grillmaster.core.srt import SrtBlock
    from grillmaster.core.tool_session import ToolSession
    from grillmaster.telop.select import TelopSubtitles

TASK_NAME = "notes"
PROBE_TASK_NAME = "notes_probe"

# How long a note stays: a second to find it, then its text at a reading
# pace slower than the dialogue's (it is read beside it), within bounds.
READ_CHARS_PER_S = 6.0
FIND_S = 1.0
MIN_SHOWN_S = 3.0
MAX_SHOWN_S = 10.0


class NoteDraft(StrictModel):
    """`block`: the subtitle block the note shows with; `term`: what it
    explains, as written in that block."""

    block: int
    term: str
    text: str
    evidence: str


class NotesAnswer(StrictModel):
    notes: list[NoteDraft]


class NoteCandidate(StrictModel):
    """`annotate`: whether the agent would write a note for it."""

    block: int
    term: str
    annotate: bool
    reason: str


class NotesProbe(StrictModel):
    """The probe's answer: everything weighed, noted or not."""

    candidates: list[NoteCandidate]


class Note(StrictModel):
    """One note, on screen from `start` to `end` (seconds)."""

    block: int
    start: float
    end: float
    term: str
    text: str
    evidence: str


class Notes(StrictModel):
    """`.subs/notes.cht.json`, in time order."""

    items: list[Note] = Field(default_factory=list[Note])


@dataclass(frozen=True, slots=True)
class NotesInputs:
    """`transcript` is `ja.srt`, `subtitles` the final Chinese
    subtitles of the same blocks (`video.cht.srt`); `on_screen` the
    on-screen text table (`telop.reference`), `None` without a scan;
    `reports` the earlier passes' reports, `(pass, text)`;
    `broadcast_date`, when known, dates the current events the talk means."""

    broadcast_date: date | None
    transcript: Sequence[SrtBlock]
    subtitles: Sequence[SrtBlock]
    telops: TelopSubtitles
    on_screen: str | None
    briefing: Briefing
    reports: Sequence[tuple[str, str]]
    program_instruction: str


def render_dialogue(
    transcript: Sequence[SrtBlock], subtitles: Sequence[SrtBlock]
) -> str:
    """Each block's number, start (seconds), Japanese and final Chinese."""
    zh = {block.index: block.text for block in subtitles}
    rows = [
        f"{block.index}\t{block.time_range.start:.1f}\t{flat(block.text)}\t"
        f"{flat(zh.get(block.index, ''))}"
        for block in transcript
    ]
    return "block\tseconds\tja\tzh\n" + "\n".join(rows)


def render_telops(telops: TelopSubtitles) -> str:
    """The translated cards: when each is up, what it says and its
    translation."""
    rows = [
        f"{item.start:.1f}-{item.end:.1f}\t{flat(item.ja)}\t{flat(item.zh)}"
        for item in telops.items
    ]
    return "seconds\tja\tzh\n" + "\n".join(rows)


def _prompt(inputs: NotesInputs) -> str:
    return join_sections(
        (
            f"## Broadcast\n\nAired {inputs.broadcast_date.isoformat()}."
            if inputs.broadcast_date is not None
            else "## Broadcast\n\nThe air date is unknown."
        ),
        f"## Dialogue ({len(inputs.transcript)} blocks)\n\n"
        f"```tsv\n{render_dialogue(inputs.transcript, inputs.subtitles)}\n```",
        (
            f"## Translated cards ({len(inputs.telops.items)})\n\n"
            f"```tsv\n{render_telops(inputs.telops)}\n```"
            if inputs.telops.items
            else "## Translated cards\n\nNone: the viewer reads only the subtitles."
        ),
        (
            f"## On-screen text (OCR; evidence only)\n\n```tsv\n{inputs.on_screen}\n```"
            if inputs.on_screen is not None
            else None
        ),
        f"## Briefing\n\n```json\n{inputs.briefing.render_for_prompt()}\n```",
        *(
            f"## Earlier pass: {name}\n\n{text.strip()}"
            for name, text in inputs.reports
        ),
        frames_guidance(),
    )


def _task[T: StrictModel](
    inputs: NotesInputs,
    *,
    name: str,
    closing: str,
    output: type[T],
    validator: Callable[[Mapping[int, str]], Callable[[T], None]],
    requires: frozenset[Capability],
    session_dir: Path,
    tools: ToolSession,
) -> AgentTask[T]:
    """The notes instructions closed by `closing`, over the same prompt."""
    return AgentTask(
        name=name,
        role=Role.EDITOR,
        instructions=join_sections(
            load_prompt(__package__, "notes.md"),
            load_prompt(__package__, closing),
            render_program_instruction(inputs.program_instruction),
        ),
        prompt=_prompt(inputs),
        session_dir=session_dir,
        workdir=None,
        output=SchemaOutput(output),
        tools=tools,
        validate=validator(block_texts(inputs.transcript, inputs.subtitles)),
        requires=requires,
    )


def build_notes_task(
    inputs: NotesInputs, *, session_dir: Path, tools: ToolSession
) -> AgentTask[NotesAnswer]:
    """The notes call; the backend must search the web."""
    return _task(
        inputs,
        name=TASK_NAME,
        closing="answer.md",
        output=NotesAnswer,
        validator=make_validator,
        requires=frozenset({Capability.WEB_SEARCH}),
        session_dir=session_dir,
        tools=tools,
    )


def build_probe_task(
    inputs: NotesInputs, *, session_dir: Path, tools: ToolSession
) -> AgentTask[NotesProbe]:
    """The dry run: the same rules, no research, every candidate weighed."""
    return _task(
        inputs,
        name=PROBE_TASK_NAME,
        closing="probe.md",
        output=NotesProbe,
        validator=make_probe_validator,
        requires=frozenset(),
        session_dir=session_dir,
        tools=tools,
    )


def _squash(text: str) -> str:
    """`text` without whitespace or line breaks, for finding a quoted term."""
    return "".join(text.split())


def block_texts(
    transcript: Iterable[SrtBlock], subtitles: Iterable[SrtBlock]
) -> dict[int, str]:
    """Each block's Japanese and Chinese, squashed, for anchoring a term."""
    texts: dict[int, str] = {}
    for block in (*transcript, *subtitles):
        texts[block.index] = texts.get(block.index, "") + "\n" + _squash(block.text)
    return texts


def _anchor_problems(block: int, term: str, texts: Mapping[int, str]) -> list[str]:
    """What is wrong with `term` as quoted from `block`; an unknown block is
    `id_coverage`'s to report."""
    if block not in texts:
        return []
    if not _squash(term):
        return [f"block {block}: the term is empty"]
    if _squash(term) not in texts[block]:
        return [
            (
                f"block {block}: {term!r} is not written in that block's ja or zh; "
                "quote the term as it appears there, or anchor the note to the "
                "block that says it"
            )
        ]
    return []


def make_validator(texts: Mapping[int, str]) -> Callable[[NotesAnswer], None]:
    """`ValidationFailure` for a note whose block is not in the table or
    already has one, whose term that block does not write, whose text is
    empty, broken into lines or longer than the layer's `MAX_ROWS` rows, or
    without evidence (`texts`: `block_texts`)."""

    def validate(answer: NotesAnswer) -> None:
        coverage = id_coverage(
            texts,
            ((note.block, note.text.strip()) for note in answer.notes),
            require_all=False,
        )
        problems = [
            *(f"block {i} is not in the dialogue table" for i in coverage.unknown),
            *(f"block {i} has more than one note" for i in coverage.duplicate),
            *(f"block {i}: the text is empty" for i in coverage.empty),
        ]
        for note in answer.notes:
            problems += _anchor_problems(note.block, note.term, texts)
            text = note.text.strip()
            if "\n" in text:
                problems.append(f"block {note.block}: the text has a line break")
            elif text and len(rows(text)) > MAX_ROWS:
                problems.append(
                    f"block {note.block}: the text takes {len(rows(text))} rows on "
                    f"screen, over {MAX_ROWS}; shorten it ({len(text)} characters)"
                )
            if not note.evidence.strip():
                problems.append(f"block {note.block}: no evidence")
        if problems:
            raise ValidationFailure.from_problems(
                "Fix these notes and return the whole list again:", problems
            )

    return validate


def make_probe_validator(texts: Mapping[int, str]) -> Callable[[NotesProbe], None]:
    """`ValidationFailure` for a candidate anchored like a bad note."""

    def validate(answer: NotesProbe) -> None:
        problems = [
            problem
            for candidate in answer.candidates
            for problem in (
                _anchor_problems(candidate.block, candidate.term, texts)
                if candidate.block in texts
                else [f"block {candidate.block} is not in the dialogue table"]
            )
        ]
        if problems:
            raise ValidationFailure.from_problems(
                "Fix these candidates and return the whole list again:", problems
            )

    return validate


def shown_for(text: str) -> float:
    """How long a note with `text` stays on screen."""
    return min(max(FIND_S + len(text) / READ_CHARS_PER_S, MIN_SHOWN_S), MAX_SHOWN_S)


def to_notes(answer: NotesAnswer, subtitles: Sequence[SrtBlock]) -> Notes:
    """The notes in time order, each from its block's start for `shown_for`
    its text. A note due while the one before is still up cuts that one
    short once it has been up `MIN_SHOWN_S`, else starts when it leaves, so
    no note drifts far from its block."""
    starts = {block.index: block.time_range.start for block in subtitles}
    drafts = sorted(answer.notes, key=lambda note: starts[note.block])
    spans: list[list[float]] = []
    for draft in drafts:
        start = starts[draft.block]
        if spans and spans[-1][1] > start:
            if start - spans[-1][0] >= MIN_SHOWN_S:
                spans[-1][1] = start
            else:
                start = spans[-1][1]
        spans.append([start, start + shown_for(draft.text.strip())])
    return Notes(
        items=[
            Note(
                block=draft.block,
                start=start,
                end=end,
                term=draft.term.strip(),
                text=draft.text.strip(),
                evidence=draft.evidence.strip(),
            )
            for draft, (start, end) in zip(drafts, spans, strict=True)
        ]
    )


def render_probe(probe: NotesProbe, subtitles: Sequence[SrtBlock]) -> str:
    """The probe as a Markdown table, in block order, noted ones marked."""
    by_index = {block.index: block for block in subtitles}
    rows = [
        f"| {candidate.block} | {by_index[candidate.block].time_range.start:.1f} "
        f"| {'✓' if candidate.annotate else '—'} | {_cell(candidate.term)} "
        f"| {_cell(by_index[candidate.block].text)} | {_cell(candidate.reason)} |"
        for candidate in sorted(probe.candidates, key=lambda c: c.block)
    ]
    noted = sum(candidate.annotate for candidate in probe.candidates)
    return (
        f"# Notes probe\n\n{noted} of {len(probe.candidates)} candidates would "
        "be noted.\n\n| block | seconds | note | term | zh | reason |\n"
        "| --- | --- | --- | --- | --- | --- |\n" + "\n".join(rows) + "\n"
    )


def _cell(text: str) -> str:
    return flat(text).replace("|", "\\|")
