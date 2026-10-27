from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.fakes import make_briefing

from grillmaster.agents.errors import ValidationFailure
from grillmaster.core.srt import SrtBlock
from grillmaster.core.timecode import format_timecode_line
from grillmaster.core.tool_session import ToolSession
from grillmaster.telop.scan import CardKind, CardLine, TelopCard
from grillmaster.telop.select import (
    KeptTelop,
    SelectInputs,
    TelopSelection,
    build_select_task,
    make_validator,
    render_cards,
    to_subtitles,
)

if TYPE_CHECKING:
    from pathlib import Path

CARDS = [
    TelopCard(
        id=4,
        start=20.5,
        end=26.0,
        kind=CardKind.CANDIDATE,
        lines=[CardLine(text="相変わらず何も知らない", box=(0.03, 0.85, 0.56, 0.95))],
    ),
    TelopCard(
        id=9,
        start=1.0,
        end=80.0,
        kind=CardKind.CANDIDATE,
        lines=[
            CardLine(text="何度も転生?", box=(0.03, 0.03, 0.3, 0.06)),
            CardLine(text="亀姫の前世", box=(0.03, 0.06, 0.3, 0.1)),
        ],
    ),
]


def test_the_card_table_gives_seconds_and_position():
    assert render_cards(CARDS).splitlines() == [
        "id\tseconds\tposition\ttext",
        "4\t20.5-26.0\tbottom-left\t相変わらず何も知らない",
        "9\t1.0-80.0\ttop-left\t何度も転生? / 亀姫の前世",
    ]


def test_the_task_carries_the_cards_transcript_briefing_and_program_text(
    tmp_path: Path,
):
    inputs = SelectInputs(
        cards=CARDS,
        transcript=[SrtBlock(1, format_timecode_line(19.0, 21.0), "いや\n知らない")],
        briefing=make_briefing(names=("濱家",)),
        program_instruction="Keep every 説 card.",
    )
    tools = ToolSession(
        project_root=tmp_path, frames=None, check_srt=None, watch_clip=None
    )

    task = build_select_task(inputs, session_dir=tmp_path / "session", tools=tools)

    assert task.workdir is None
    assert "[19.0] いや 知らない" in task.prompt
    assert "相変わらず何も知らない" in task.prompt
    assert "濱家" in task.prompt
    assert "Keep every 説 card." in task.instructions


def test_the_validator_rejects_unknown_repeated_and_empty_entries():
    validate = make_validator({4, 9})
    validate(TelopSelection(kept=[KeptTelop(id=4, zh="還是什麼都不知道")]))
    validate(TelopSelection(kept=[]))

    answer = TelopSelection(
        kept=[
            KeptTelop(id=5, zh="x"),
            KeptTelop(id=4, zh="a"),
            KeptTelop(id=4, zh="b"),
            KeptTelop(id=9, zh=" "),
        ]
    )
    with pytest.raises(ValidationFailure) as failure:
        validate(answer)
    message = str(failure.value)
    assert "id 5 is not a card" in message
    assert "id 4 is listed more than once" in message
    assert "id 9 has an empty zh" in message


def test_kept_cards_become_subtitles_in_time_order():
    selection = TelopSelection(
        kept=[KeptTelop(id=4, zh=" 還是什麼都不知道 "), KeptTelop(id=9, zh="轉生")]
    )

    subtitles = to_subtitles(CARDS, selection)

    assert [(item.id, item.start, item.zh) for item in subtitles.items] == [
        (9, 1.0, "轉生"),
        (4, 20.5, "還是什麼都不知道"),
    ]
    assert subtitles.items[0].ja == "何度も転生? / 亀姫の前世"
    assert subtitles.items[0].box == (0.03, 0.03, 0.3, 0.1)
