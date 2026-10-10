"""Live-chat replay data model shared by parse, translate, and render."""

from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, Field


class _JsonFile(BaseModel):
    """A model persisted as one UTF-8 JSON file."""

    @classmethod
    def read(cls, path: Path) -> Self:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")


class ChatMessage(BaseModel):
    """One replayed chat message on the `video.mp4` timeline.

    ``id`` is the message's position in the normalized log and is what the
    translation batches key on. ``text`` is the original message with emoji
    runs removed; a Super Chat may carry only an ``amount`` and empty text.
    """

    id: int
    seconds: float
    author: str
    text: str
    kind: Literal["text", "paid"] = "text"
    amount: str | None = None


class ChatLog(_JsonFile):
    """Normalized chat messages, ordered by time (`.live_chat/messages.json`)."""

    messages: list[ChatMessage] = Field(default_factory=list)


class TranslatedChatMessage(ChatMessage):
    """A chat message with its Traditional Chinese display text."""

    translation: str


class TranslatedChatLog(_JsonFile):
    """Translated chat consumed by packaging (`chat.cht.json`)."""

    messages: list[TranslatedChatMessage] = Field(default_factory=list)
