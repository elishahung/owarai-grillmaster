"""Live-chat replay: normalize, translate, and render as a burned-in chat panel.

Opt-in per run (`--chat`); projects without it never touch this package. The
download itself lives in `services.ytdlp.download_live_chat`.
"""

from .parse import parse_live_chat
from .render import DEFAULT_CHAT_LAYOUT, ChatLayout, chat_burn_plan
from .translate import ChatTranslationInputs, translate_live_chat

__all__ = [
    "DEFAULT_CHAT_LAYOUT",
    "ChatLayout",
    "ChatTranslationInputs",
    "chat_burn_plan",
    "parse_live_chat",
    "translate_live_chat",
]
