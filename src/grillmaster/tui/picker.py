"""A full-screen, searchable single-choice list, run before any dashboard.

`pick(title, choices)` returns the chosen value, or `None` when the user
cancels. Typing filters the list: every whitespace-separated term must
appear in a choice's label (case-insensitive). Up/Down move the highlight
while the search box keeps focus; Enter picks, Escape cancels.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from textual.app import App
from textual.binding import Binding, BindingType
from textual.content import Content
from textual.widgets import Footer, Input, Label, OptionList
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from collections.abc import Sequence

    from textual.app import ComposeResult


@dataclass(frozen=True, slots=True)
class Choice[T]:
    label: str
    value: T


def matches(label: str, query: str) -> bool:
    """Whether every term of `query` occurs in `label`, ignoring case."""
    folded = label.casefold()
    return all(term in folded for term in query.casefold().split())


class PickerApp[T](App[T | None]):
    CSS = """
    #title { padding: 0 1; text-style: bold; }
    #choices { height: 1fr; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "move(-1)", "Up", show=False, priority=True),
        Binding("down", "move(1)", "Down", show=False, priority=True),
        Binding("enter", "choose", "Pick", priority=True),
    ]

    def __init__(self, title: str, choices: Sequence[Choice[T]]) -> None:
        super().__init__()
        self._heading = title
        self._choices = tuple(choices)
        self._shown: tuple[Choice[T], ...] = self._choices

    def compose(self) -> ComposeResult:
        yield Label(self._heading, id="title")
        yield Input(placeholder="Search", id="search")
        yield OptionList(*self._options(), id="choices")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#search", Input).focus()
        self._highlight_first()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._shown = tuple(c for c in self._choices if matches(c.label, event.value))
        choices = self.query_one("#choices", OptionList)
        choices.clear_options()
        choices.add_options(self._options())
        self._highlight_first()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.exit(self._shown[event.option_index].value)

    def action_move(self, step: int) -> None:
        choices = self.query_one("#choices", OptionList)
        if not self._shown:
            return
        current = choices.highlighted or 0
        choices.highlighted = max(0, min(len(self._shown) - 1, current + step))

    def action_choose(self) -> None:
        highlighted = self.query_one("#choices", OptionList).highlighted
        if highlighted is not None and self._shown:
            self.exit(self._shown[highlighted].value)

    def action_cancel(self) -> None:
        self.exit(None)

    def _options(self) -> list[Option]:
        # `Content` shows the label verbatim; a plain str would parse markup.
        return [Option(Content(choice.label)) for choice in self._shown]

    def _highlight_first(self) -> None:
        self.query_one("#choices", OptionList).highlighted = 0 if self._shown else None


def pick[T](title: str, choices: Sequence[Choice[T]]) -> T | None:
    """The value of the choice the user picks; `None` when cancelled."""
    return PickerApp(title, choices).run()
