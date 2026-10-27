from __future__ import annotations

from grillmaster.telop.reference import render_reference
from grillmaster.telop.scan import CardKind, CardLine, TelopCard

BOX = (0.1, 0.1, 0.3, 0.2)


def card(id: int, start: float, text: str) -> TelopCard:  # noqa: A002 - the model's field
    return TelopCard(
        id=id,
        start=start,
        end=start + 2.0,
        kind=CardKind.CANDIDATE,
        lines=[CardLine(text=line, box=BOX) for line in text.split(" / ")],
    )


def test_repeats_share_a_row():
    table = render_reference(
        [
            card(1, 10.0, "東京都"),
            card(2, 12.0, "初期メン!!"),
            card(3, 50.0, "東京都"),
        ]
    )

    assert table.splitlines() == [
        "seconds\ttext",
        "10.0-12.0, 50.0-52.0\t東京都",
        "12.0-14.0\t初期メン!!",
    ]
