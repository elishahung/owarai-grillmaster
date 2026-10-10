"""Pilot tests for the searchable picker."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from grillmaster.tui.picker import Choice, PickerApp, matches

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from textual.pilot import Pilot

CHOICES = (
    Choice("10-09 21:00  261008_ep2_[Owarai] Final", "ep2"),
    Choice("10-05 12:00  261004_ep1_Owarai Semi", "ep1"),
    Choice("10-03 08:00  etc_ep0_Other show", "ep0"),
)


def _pick(steps: Callable[[Pilot[str | None]], Awaitable[None]]) -> str | None:
    async def main() -> str | None:
        app = PickerApp("Parent", CHOICES)
        async with app.run_test() as pilot:
            await pilot.pause()
            await steps(pilot)
            await pilot.pause()
        return app.return_value

    return asyncio.run(main())  # noqa: TID251 - Textual's pilot needs an event loop


@pytest.mark.parametrize(
    ("query", "expected"),
    [("", True), ("owarai FINAL", True), ("ep2 semi", False), ("[owarai]", True)],
)
def test_every_term_must_match(query: str, expected: bool):
    assert matches(CHOICES[0].label, query) is expected


def test_enter_picks_the_first_choice():
    async def steps(pilot: Pilot[str | None]) -> None:
        await pilot.press("enter")

    assert _pick(steps) == "ep2"


def test_arrows_move_the_highlight_from_the_search_box():
    async def steps(pilot: Pilot[str | None]) -> None:
        await pilot.press("down", "down", "down", "up", "enter")

    assert _pick(steps) == "ep1"


def test_typing_filters_the_list():
    async def steps(pilot: Pilot[str | None]) -> None:
        await pilot.press(*"owarai semi", "enter")

    assert _pick(steps) == "ep1"


def test_enter_on_no_match_picks_nothing_and_escape_cancels():
    async def steps(pilot: Pilot[str | None]) -> None:
        await pilot.press(*"zzz", "enter", "escape")

    assert _pick(steps) is None
