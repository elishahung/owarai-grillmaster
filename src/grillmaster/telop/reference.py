"""The on-screen text as a reference table for the passes that check the
dialogue subtitles (glossary check, proofread): edited captions often spell
a name or term the ASR misheard.

It lists the cards it is given (the offered ones, `TelopCards.offered`);
a text shown several times (a map coming back) is one row with every time
it is up, so the table stays a lead the agent can read, not noise it has to
wade through.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from grillmaster.telop.scan import TelopCard


def render_reference(cards: Sequence[TelopCard]) -> str:
    """The cards' texts with the seconds each is on screen, as a TSV."""
    times: dict[str, list[str]] = {}
    for card in cards:
        times.setdefault(card.text, []).append(f"{card.start:.1f}-{card.end:.1f}")
    rows = [f"{', '.join(spans)}\t{text}" for text, spans in times.items()]
    return "seconds\ttext\n" + "\n".join(rows)
