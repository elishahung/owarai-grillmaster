"""Live-chat replay data model shared by parse, translate and render."""

from __future__ import annotations

from typing import Literal

from grillmaster.core.models import StrictModel


class ChatMessage(StrictModel):
    """One replayed chat message on the `video.mp4` timeline.

    `id` is the message's position in the normalized log and is what the
    translation batches key on. `text` is the original message with emoji
    runs removed; a Super Chat may carry only an `amount` and empty text.
    """

    id: int
    seconds: float
    author: str
    text: str
    kind: Literal["text", "paid"] = "text"
    amount: str | None = None


class ChatLog(StrictModel):
    """Normalized chat messages, ordered by time (`work/04_chat_fetch/messages.json`)."""

    messages: list[ChatMessage]


class TranslatedChatMessage(ChatMessage):
    """A chat message with its Traditional Chinese display text."""

    translation: str


class TranslatedChatLog(StrictModel):
    """Translated chat consumed by packaging (`subs/chat.cht.json`)."""

    messages: list[TranslatedChatMessage]
