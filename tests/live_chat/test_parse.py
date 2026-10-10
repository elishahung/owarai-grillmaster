from __future__ import annotations

from typing import TYPE_CHECKING

from tests.fakes import replay_line, text_item

from grillmaster.live_chat.parse import parse_live_chat

if TYPE_CHECKING:
    from pathlib import Path

_EMOJI = {"emoji": {"emojiId": "x", "shortcuts": [":_cookie:"]}}


def _write(tmp_path: Path, *lines: str) -> Path:
    raw = tmp_path / "live_chat.jsonl"
    raw.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return raw


def test_keeps_text_and_paid_drops_emoji_and_rebases_section(tmp_path: Path) -> None:
    raw = _write(
        tmp_path,
        replay_line(5_000, text_item("@early", {"text": "前"})),
        replay_line(
            12_500, text_item("@a", {"text": "池田 "}, _EMOJI, {"text": "ありがとう"})
        ),
        replay_line(13_000, text_item("@emoji-only", _EMOJI)),
        replay_line(
            11_000,
            {
                "liveChatPaidMessageRenderer": {
                    "authorName": {"simpleText": "@donor"},
                    "purchaseAmountText": {"simpleText": "¥800"},
                }
            },
        ),
        replay_line(
            14_000,
            {"liveChatMembershipItemRenderer": {"authorName": {"simpleText": "@m"}}},
        ),
        replay_line(30_000, text_item("@late", {"text": "後"})),
    )

    log = parse_live_chat(raw, section_start=10.0, section_end=20.0)

    assert [
        (m.id, m.seconds, m.author, m.text, m.kind, m.amount) for m in log.messages
    ] == [
        (0, 1.0, "@donor", "", "paid", "¥800"),
        (1, 2.5, "@a", "池田 ありがとう", "text", None),
    ]


def test_without_section_keeps_replay_order_for_equal_offsets(tmp_path: Path) -> None:
    raw = _write(
        tmp_path,
        replay_line(2_000, text_item(" @b ", {"text": "  二\n つ "})),
        replay_line(1_000, text_item("@a", {"text": "一"})),
        replay_line(2_000, text_item("@c", {"text": "三"})),
        '{"replayChatItemAction": {"actions": []}}',
        "",
    )

    log = parse_live_chat(raw)

    assert [(m.id, m.seconds, m.author, m.text) for m in log.messages] == [
        (0, 1.0, "@a", "一"),
        (1, 2.0, "@b", "二 つ"),
        (2, 2.0, "@c", "三"),
    ]
