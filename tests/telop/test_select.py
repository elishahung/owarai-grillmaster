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
        kind=CardKind.SPEECH,
        lines=[
            CardLine(
                text="何度も転生?", box=(0.03, 0.03, 0.3, 0.06), echoes_speech=True
            ),
            CardLine(text="亀姫の前世", box=(0.03, 0.06, 0.3, 0.1), echoes_speech=True),
        ],
    ),
]
# A name plate the rules joined to the caption under it; the caption repeats
# the speech.
PLATE = TelopCard(
    id=15,
    start=2384.5,
    end=2388.0,
    kind=CardKind.CANDIDATE,
    lines=[
        CardLine(text="ラランド", box=(0.832, 0.577, 0.928, 0.652)),
        CardLine(text="サーヤ", box=(0.826, 0.622, 0.943, 0.724)),
        CardLine(
            text="最後の大根痺れました",
            box=(0.003, 0.731, 0.996, 0.999),
            echoes_speech=True,
        ),
    ],
)


def test_the_card_table_numbers_lines_and_lists_the_speech_echoes():
    assert render_cards([*CARDS, PLATE]).splitlines() == [
        "id\tseconds\tposition\techoes_speech\ttext",
        "4\t20.5-26.0\tbottom-left\t\t[1] 相変わらず何も知らない",
        "9\t1.0-80.0\ttop-left\t1,2\t[1] 何度も転生? / [2] 亀姫の前世",
        (
            "15\t2384.5-2388.0\tbottom-center\t3\t"
            "[1] ラランド / [2] サーヤ / [3] 最後の大根痺れました"
        ),
    ]


def test_the_task_carries_the_cards_dialogue_briefing_and_program_text(
    tmp_path: Path,
):
    timecode = format_timecode_line(19.0, 21.0)
    inputs = SelectInputs(
        cards=CARDS,
        transcript=[SrtBlock(1, timecode, "いや\n知らない")],
        subtitles=[SrtBlock(1, timecode, "不是\n我不知道")],
        briefing=make_briefing(names=("濱家",)),
        program_instruction="Keep every 説 card.",
    )
    tools = ToolSession(
        project_root=tmp_path, frames=None, check_srt=None, watch_clip=None
    )

    task = build_select_task(inputs, session_dir=tmp_path / "session", tools=tools)

    assert task.workdir is None
    assert "19.0\tいや / 知らない\t不是 / 我不知道" in task.prompt
    assert "相変わらず何も知らない" in task.prompt
    assert "濱家" in task.prompt
    assert "Keep every 説 card." in task.instructions


def test_the_validator_rejects_unknown_repeated_and_empty_entries():
    validate = make_validator({4: 1, 9: 2})
    validate(TelopSelection(kept=[KeptTelop(id=4, lines=[1], zh="還是什麼都不知道")]))
    validate(TelopSelection(kept=[]))

    answer = TelopSelection(
        kept=[
            KeptTelop(id=5, lines=[1], zh="x"),
            KeptTelop(id=4, lines=[1], zh="a"),
            KeptTelop(id=4, lines=[1], zh="b"),
            KeptTelop(id=9, lines=[1, 2], zh=" "),
        ]
    )
    with pytest.raises(ValidationFailure) as failure:
        validate(answer)
    message = str(failure.value)
    assert "id 5 is not a card" in message
    assert "id 4 is listed more than once" in message
    assert "id 9 has an empty zh" in message


def test_the_validator_rejects_missing_repeated_and_unknown_lines():
    validate = make_validator({9: 2})

    for lines, problem in [
        ([], "id 9 lists no lines"),
        ([1, 1], "id 9 lists a line more than once"),
        ([2, 3], "id 9 has only lines 1-2"),
    ]:
        with pytest.raises(ValidationFailure) as failure:
            validate(TelopSelection(kept=[KeptTelop(id=9, lines=lines, zh="轉生")]))
        assert problem in str(failure.value)


def test_kept_cards_become_subtitles_in_time_order():
    selection = TelopSelection(
        kept=[
            KeptTelop(id=4, lines=[1], zh=" 還是什麼都不知道 "),
            KeptTelop(id=9, lines=[1, 2], zh="轉生"),
        ]
    )

    subtitles = to_subtitles(CARDS, selection)

    assert [(item.id, item.start, item.zh) for item in subtitles.items] == [
        (9, 1.0, "轉生"),
        (4, 20.5, "還是什麼都不知道"),
    ]
    assert subtitles.items[0].ja == "何度も転生? / 亀姫の前世"
    assert subtitles.items[0].boxes == [(0.03, 0.03, 0.3, 0.06), (0.03, 0.06, 0.3, 0.1)]
    assert subtitles.items[0].beside == []


def test_a_translation_of_some_lines_sits_on_those_lines():
    selection = TelopSelection(
        kept=[KeptTelop(id=15, lines=[2, 1], zh="LaLaLand 紗彩")]
    )

    (item,) = to_subtitles([PLATE], selection).items

    assert item.ja == "ラランド / サーヤ"
    assert item.boxes == [(0.832, 0.577, 0.928, 0.652), (0.826, 0.622, 0.943, 0.724)]
    # The caption left out is still on screen: the translation avoids it.
    assert item.beside == [(0.003, 0.731, 0.996, 0.999)]


def test_a_translation_reading_like_its_card_is_dropped():
    place = TelopCard(
        id=12,
        start=30.0,
        end=34.0,
        kind=CardKind.CANDIDATE,
        lines=[
            CardLine(text="東京都", box=(0.1, 0.7, 0.2, 0.75)),
            CardLine(text="六本木", box=(0.1, 0.76, 0.2, 0.8)),
        ],
    )
    selection = TelopSelection(
        kept=[
            KeptTelop(id=12, lines=[1, 2], zh="東京都\n六本木"),
            KeptTelop(id=4, lines=[1], zh="還是不知道"),
        ]
    )

    subtitles = to_subtitles([*CARDS, place], selection)

    assert [item.id for item in subtitles.items] == [4]
