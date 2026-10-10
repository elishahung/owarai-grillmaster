"""Video sources: per-platform policy and extras, yt-dlp, captions, chat replay.

Platform differences (cookie policy, talent and on-air metadata, broadcast
date rules, pre-download hooks) live in one `SourcePlatform` per platform,
looked up through `sources.registry`; ID/URL parsing is `core.source_id`.
yt-dlp and the platform HTTP APIs sit behind injectable seams (`YtDlp`,
`JsonHttp`), so nothing here touches the network in tests.
"""
