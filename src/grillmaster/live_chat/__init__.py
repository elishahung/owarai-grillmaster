"""Opt-in live-chat replay (`--chat`): normalize, translate, render as a panel.

`parse` turns yt-dlp's replay track into a `ChatLog` on the `video.mp4`
timeline; `translate` sends Japanese messages to the chat agent in balanced
batches (through the caller's `AgentRunner`) and polishes the whole stream
once; `render` writes the scrolling chat-panel ASS for a `ChatLayout` and
tells packaging where that layout puts the picture and the dialogue. The
download lives in `sources.live_chat`; the stages in `stages.chat_fetch` and
`stages.chat_translate`.
"""
