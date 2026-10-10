"""The normalized agent event vocabulary (borrowed from ACP) and its summaries.

Adapters translate each CLI's own stream into these events; the runner turns
them into `AgentActivity` for the TUI through `summarize`. Token usage is
not an event: it travels with each turn's `FinalOutput`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from grillmaster.events.types import ActivityKind

if TYPE_CHECKING:
    from collections.abc import Mapping

SUMMARY_LIMIT = 80
_ARG_LIMIT = 40


@dataclass(frozen=True, slots=True)
class Thought:
    text: str


@dataclass(frozen=True, slots=True)
class ToolCall:
    """`name` is the bare tool name; MCP calls drop their server prefix."""

    name: str
    args: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolResult:
    name: str
    ok: bool = True


@dataclass(frozen=True, slots=True)
class Message:
    """Visible assistant text. The runner holds the latest one back: the last
    message of a turn is the final output and is summarized by length only."""

    text: str


type AgentEvent = Thought | ToolCall | ToolResult | Message


def normalize_usage(raw: object, names: Mapping[str, str]) -> dict[str, int]:
    """Pick integer counters out of a backend usage record, renamed by `names`
    (backend key -> normalized key: `input_tokens`, `output_tokens`,
    `cached_input_tokens`, `reasoning_tokens`)."""
    if not isinstance(raw, dict):
        return {}
    counters: dict[str, object] = raw
    return {
        ours: value
        for theirs, ours in names.items()
        if isinstance(value := counters.get(theirs), int)
        and not isinstance(value, bool)
    }


def summarize(event: AgentEvent) -> tuple[ActivityKind, str] | None:
    """One activity line for the TUI; `None` for events it does not show."""
    match event:
        case Thought(text=text):
            line = first_line(text)
            return (ActivityKind.THOUGHT, line) if line else None
        case ToolCall(name=name, args=args):
            shown = abbreviate_args(args)
            return ActivityKind.TOOL_CALL, f"{name} {shown}".rstrip()
        case ToolResult(name=name, ok=ok):
            return ActivityKind.TOOL_RESULT, f"{name} {'ok' if ok else 'failed'}"
        case Message(text=text):
            line = first_line(text)
            return (ActivityKind.MESSAGE, line) if line else None


def summarize_final(text: str) -> tuple[ActivityKind, str]:
    """The activity line for a turn's final output: its length, not its text."""
    return ActivityKind.MESSAGE, f"final output ({len(text)} chars)"


def first_line(text: str) -> str:
    """The first non-blank line, cut to `SUMMARY_LIMIT` characters."""
    for line in text.splitlines():
        if stripped := line.strip():
            return _cut(stripped, SUMMARY_LIMIT)
    return ""


def abbreviate_args(args: Mapping[str, object]) -> str:
    """Argument values only, each compacted, e.g. `62.5, 70, 77` for one list."""
    parts = [_compact(value) for value in args.values()]
    return _cut(", ".join(part for part in parts if part), SUMMARY_LIMIT)


def _compact(value: object) -> str:
    match value:
        case str():
            return _cut(" ".join(value.split()), _ARG_LIMIT)
        case list() | tuple():
            return ", ".join(_compact(item) for item in value)
        case bool() | int() | float() | None:
            return json.dumps(value)
        case _:
            return _cut(json.dumps(value, ensure_ascii=False, default=str), _ARG_LIMIT)


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
