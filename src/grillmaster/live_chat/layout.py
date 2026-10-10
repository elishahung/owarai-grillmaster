"""Where a translated chat panel goes in the packaged frame."""

from __future__ import annotations

from enum import StrEnum


class ChatLayout(StrEnum):
    # 16:9 picture letterboxed on the left, chat in its own right column,
    # dialogue in the bottom letterbox bar.
    SIDE = "side"
    # Full-frame picture with a translucent panel over its right side.
    OVERLAY = "overlay"
    # No chat: dialogue alone on the full frame.
    NONE = "none"


DEFAULT_CHAT_LAYOUT = ChatLayout.SIDE
