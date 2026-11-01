from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pytest
from tests.fakes import make_blocks, make_briefing

from grillmaster.agents.adapters.base import Capability
from grillmaster.agents.errors import ValidationFailure
from grillmaster.core.model_spec import Role
from grillmaster.core.srt import SrtBlock
from grillmaster.core.timecode import format_timecode_line
from grillmaster.core.tool_session import ToolSession
from grillmaster.notes.annotate import (
    MAX_SHOWN_S,
    MIN_SHOWN_S,
    NoteCandidate,
    NoteDraft,
    NotesAnswer,
    NotesInputs,
    NotesProbe,
    block_texts,
    build_notes_task,
    build_probe_task,
    make_probe_validator,
    make_validator,
    render_probe,
    shown_for,
    to_notes,
)
from grillmaster.telop.select import TelopSubtitles

if TYPE_CHECKING:
    from pathlib import Path

TRANSCRIPT = [
    SrtBlock(1, format_timecode_line(10.0, 12.0), "[V1] あさま山荘見すぎて"),
    SrtBlock(2, format_timecode_line(20.0, 22.0), "[V2] ロス疑惑の\n三浦さん"),
    SrtBlock(3, format_timecode_line(22.5, 23.0), "[V2] ね"),
]
SUBTITLES = [
    SrtBlock(1, format_timecode_line(10.0, 12.0), "淺間山莊看太多"),
    SrtBlock(2, format_timecode_line(20.0, 22.0), "洛杉磯疑案的\n三浦桑"),
    SrtBlock(3, format_timecode_line(22.5, 23.0), "對吧"),
]
TEXTS = block_texts(TRANSCRIPT, SUBTITLES)


def draft(
    block: int = 2, term: str = "三浦", text: str = "三浦和義：說明"
) -> NoteDraft:
    return NoteDraft(block=block, term=term, text=text, evidence="https://example")


def problems(*notes: NoteDraft) -> str:
    with pytest.raises(ValidationFailure) as failure:
        make_validator(TEXTS)(NotesAnswer(notes=list(notes)))
    return str(failure.value)


def test_a_note_quoting_its_block_in_either_language_passes():
    make_validator(TEXTS)(
        NotesAnswer(
            notes=[
                draft(),
                # Across the block's line break, in the Chinese.
                draft(block=1, term="淺間 山莊", text="淺間山莊事件：說明"),
            ]
        )
    )


@pytest.mark.parametrize(
    ("note", "expected"),
    [
        (draft(block=9), "block 9 is not in the dialogue table"),
        (draft(term="田中"), "'田中' is not written in that block"),
        (draft(term=" "), "the term is empty"),
        (draft(text=""), "the text is empty"),
        (draft(text="三浦：\n說明"), "the text has a line break"),
        (draft(text="三" * 70), "takes 3 rows on screen, over 2; shorten it"),
        (
            NoteDraft(block=2, term="三浦", text="三浦：說明", evidence=" "),
            "no evidence",
        ),
    ],
)
def test_bad_notes_are_a_repair_round(note: NoteDraft, expected: str):
    assert expected in problems(note)


def test_one_note_per_block():
    assert "block 2 has more than one note" in problems(draft(), draft(term="疑惑"))


def test_probe_candidates_are_anchored_like_notes():
    validate = make_probe_validator(TEXTS)
    validate(
        NotesProbe(
            candidates=[NoteCandidate(block=1, term="淺間", annotate=False, reason="")]
        )
    )
    with pytest.raises(ValidationFailure, match="not written in that block"):
        validate(
            NotesProbe(
                candidates=[
                    NoteCandidate(block=3, term="三浦", annotate=True, reason="")
                ]
            )
        )


def test_shown_for_reads_slower_than_the_dialogue_within_bounds():
    assert shown_for("短") == MIN_SHOWN_S
    assert shown_for("字" * 30) == pytest.approx(6.0)
    assert shown_for("字" * 200) == MAX_SHOWN_S


def test_sixty_characters_fit_the_two_rows():
    make_validator(TEXTS)(NotesAnswer(notes=[draft(text="三浦和義：" + "說" * 55)]))


def test_notes_follow_one_another():
    answer = NotesAnswer(
        notes=[
            draft(text="三浦和義：" + "說" * 25),
            draft(block=1, term="淺間", text=" 淺間山莊事件：說明 "),
            draft(block=3, term="對吧", text="對吧：說明"),
        ]
    )

    notes = to_notes(answer, SUBTITLES)

    first, second, third = notes.items
    assert [note.block for note in notes.items] == [1, 2, 3]
    assert (first.start, first.text) == (10.0, "淺間山莊事件：說明")
    assert second.start == 20.0
    # Due while the one before is still up: it waits for that one to leave.
    assert third.start == second.end > 22.5


def test_a_note_due_later_cuts_the_one_before_short_rather_than_drift():
    blocks = [
        SrtBlock(1, format_timecode_line(0.0, 1.0), "一"),
        SrtBlock(2, format_timecode_line(5.0, 6.0), "二"),
    ]
    answer = NotesAnswer(
        notes=[
            draft(block=1, term="一", text="一：" + "說" * 60),
            draft(block=2, term="二", text="二：說明"),
        ]
    )

    first, second = to_notes(answer, blocks).items

    assert (first.start, first.end) == (0.0, 5.0)
    assert second.start == 5.0


def test_the_prompt_carries_every_input(tmp_path: Path):
    inputs = NotesInputs(
        broadcast_date=date(2026, 10, 28),
        transcript=TRANSCRIPT,
        subtitles=SUBTITLES,
        telops=TelopSubtitles(),
        on_screen="seconds\ttext\n1.0-2.0\tロス疑惑",
        briefing=make_briefing(),
        reports=(("proofread", "found online"),),
        program_instruction="Keep it short.",
    )
    tools = ToolSession(
        project_root=tmp_path, frames=None, check_srt=None, watch_clip=None
    )

    task = build_notes_task(inputs, session_dir=tmp_path / "session", tools=tools)

    assert task.role is Role.EDITOR
    assert task.requires == frozenset({Capability.WEB_SEARCH})
    assert "2\t20.0\t[V2] ロス疑惑の / 三浦さん\t洛杉磯疑案的 / 三浦桑" in task.prompt
    assert task.prompt.startswith("## Broadcast\n\nAired 2026-10-28.")
    assert "None: the viewer reads only the subtitles." in task.prompt
    assert "1.0-2.0\tロス疑惑" in task.prompt
    assert "## Earlier pass: proofread\n\nfound online" in task.prompt
    assert "## When a note is needed" in task.instructions
    assert "## Research and writing" in task.instructions
    assert "Keep it short." in task.instructions

    probe = build_probe_task(inputs, session_dir=tmp_path / "probe", tools=tools)

    assert probe.requires == frozenset()
    assert "## Dry run" in probe.instructions
    assert "## Research and writing" not in probe.instructions
    assert probe.prompt == task.prompt


def test_the_probe_reads_as_a_table_in_block_order():
    probe = NotesProbe(
        candidates=[
            NoteCandidate(block=2, term="三浦", annotate=True, reason="缺|背景"),
            NoteCandidate(block=1, term="淺間", annotate=False, reason="已解釋"),
        ]
    )

    report = render_probe(probe, make_blocks(2))

    assert "1 of 2 candidates would be noted." in report
    assert report.index("| 1 | 2.0 | — | 淺間 |") < report.index(
        "| 2 | 4.0 | ✓ | 三浦 | line 2 | 缺\\|背景 |"
    )
