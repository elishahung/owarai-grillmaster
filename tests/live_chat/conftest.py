"""Builders for the live-chat tests: the golden translated log."""

from __future__ import annotations

from typing import Any

from grillmaster.live_chat.schema import TranslatedChatLog, TranslatedChatMessage


def golden_log() -> TranslatedChatLog:
    """A short stream exercising every render path: wrapping and truncation,
    escaped override syntax, Super Chats with and without text, same-
    centisecond arrivals, and enough rows to scroll past the panel top."""
    rows: list[dict[str, Any]] = [
        {"seconds": 1.0, "author": "@a", "translation": "池田{\\b1}"},
        {
            "seconds": 2.0,
            "author": "@donor",
            "translation": "",
            "kind": "paid",
            "amount": "¥800",
        },
        {"seconds": 3.5, "author": "@長い名前" * 8, "translation": "笑死" * 60},
        {"seconds": 4.0, "author": "", "translation": "www"},
        {"seconds": 4.001, "author": "@same", "translation": "同一時刻\n換行"},
        {
            "seconds": 6.25,
            "author": "@big",
            "translation": "感謝招待 thanks!",
            "kind": "paid",
            "amount": "$1,000.00",
        },
    ]
    rows += [
        {
            "seconds": 7.0 + i * 0.5,
            "author": f"@v{i}",
            "translation": "好好笑" * (i + 1),
        }
        for i in range(8)
    ]
    return TranslatedChatLog(
        messages=[
            TranslatedChatMessage(id=index, text="原文", **row)
            for index, row in enumerate(rows)
        ]
    )
