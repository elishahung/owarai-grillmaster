from __future__ import annotations

import pytest

from grillmaster.agents.events import (
    Message,
    Thought,
    ToolCall,
    ToolResult,
    normalize_usage,
    summarize,
    summarize_final,
)
from grillmaster.events.types import ActivityKind


def test_thought_shows_its_first_line_cut_to_80_chars():
    text = "\n  " + "あ" * 100 + "\nsecond"
    kind, summary = summarize(Thought(text)) or (None, "")
    assert kind is ActivityKind.THOUGHT
    assert len(summary) == 80
    assert summary.endswith("…")


def test_tool_call_shows_the_name_and_abbreviated_values():
    assert summarize(ToolCall("get_frames", {"times": [62.5, 70, 77]})) == (
        ActivityKind.TOOL_CALL,
        "get_frames 62.5, 70, 77",
    )
    long_path = "C:/" + "x" * 100
    _, summary = summarize(ToolCall("check_srt", {"path": long_path})) or (None, "")
    assert len(summary) <= 80


def test_tool_call_without_arguments():
    assert summarize(ToolCall("session_info")) == (
        ActivityKind.TOOL_CALL,
        "session_info",
    )


@pytest.mark.parametrize(("ok", "word"), [(True, "ok"), (False, "failed")])
def test_tool_result(ok: bool, word: str):
    assert summarize(ToolResult("shell", ok=ok)) == (
        ActivityKind.TOOL_RESULT,
        f"shell {word}",
    )


def test_blank_text_is_not_shown():
    assert summarize(Thought("  \n")) is None
    assert summarize(Message("")) is None


def test_final_output_shows_only_its_length():
    assert summarize_final("abc") == (ActivityKind.MESSAGE, "final output (3 chars)")


def test_normalize_usage_renames_and_keeps_integers_only():
    raw = {"input_tokens": 5, "cache_read_tokens": 2, "flag": True, "other": "x"}
    names = {
        "input_tokens": "input_tokens",
        "cache_read_tokens": "cached_input_tokens",
        "flag": "flag",
    }
    assert normalize_usage(raw, names) == {"input_tokens": 5, "cached_input_tokens": 2}
    assert normalize_usage(None, names) == {}
