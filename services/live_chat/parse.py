"""Normalize a yt-dlp live-chat replay into a time-ordered `ChatLog`."""

import json
from pathlib import Path

from .schema import ChatLog, ChatMessage


def parse_live_chat(
    raw_path: Path,
    *,
    section_start: float | None = None,
    section_end: float | None = None,
) -> ChatLog:
    """Parse yt-dlp's replay JSON lines into time-ordered messages.

    Keeps plain text messages and Super Chats; membership, gift, and system
    items are dropped. Emoji runs are removed (the ASS renderer cannot draw
    colour glyphs or channel emoji images), and a text message left empty
    by that is dropped too. With a section, messages outside it are dropped
    and the rest are rebased onto the cut `video.mp4` timeline.
    """
    start = section_start or 0.0
    messages: list[ChatMessage] = []
    with raw_path.open(encoding="utf-8") as raw_file:
        for line in raw_file:
            if not line.strip():
                continue
            replay = json.loads(line).get("replayChatItemAction", {})
            offset_ms = replay.get("videoOffsetTimeMsec")
            if offset_ms is None:
                continue
            seconds = int(offset_ms) / 1000
            if seconds < start:
                continue
            if section_end is not None and seconds >= section_end:
                continue
            for action in replay.get("actions", []):
                item = action.get("addChatItemAction", {}).get("item", {})
                message = _parse_item(item, round(seconds - start, 3))
                if message is not None:
                    messages.append(message)

    # A stable sort keeps the replay's own order for equal offsets; ids are
    # the final positions.
    messages.sort(key=lambda message: message.seconds)
    for index, message in enumerate(messages):
        message.id = index
    return ChatLog(messages=messages)


def _parse_item(item: dict, seconds: float) -> ChatMessage | None:
    if "liveChatTextMessageRenderer" in item:
        renderer = item["liveChatTextMessageRenderer"]
        text = _message_text(renderer)
        if not text:
            return None
        return ChatMessage(id=0, seconds=seconds, author=_author(renderer), text=text)
    if "liveChatPaidMessageRenderer" in item:
        renderer = item["liveChatPaidMessageRenderer"]
        return ChatMessage(
            id=0,
            seconds=seconds,
            author=_author(renderer),
            text=_message_text(renderer),
            kind="paid",
            amount=renderer.get("purchaseAmountText", {}).get("simpleText"),
        )
    return None


def _author(renderer: dict) -> str:
    return renderer.get("authorName", {}).get("simpleText", "").strip()


def _message_text(renderer: dict) -> str:
    runs = renderer.get("message", {}).get("runs", [])
    text = "".join(run.get("text", "") for run in runs)
    return " ".join(text.split())
