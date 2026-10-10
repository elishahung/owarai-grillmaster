"""Source platforms and video IDs: parse what the user typed, rebuild the URL.

A video ID doubles as the project ID and directory name. YouTube IDs are
stored with a `v=` prefix because their character set overlaps Abema's, so
the platform stays recoverable from the ID alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import parse_qs, urlparse

_BILIBILI_ID_RE = re.compile(r"(BV[a-zA-Z0-9]+)")
_YOUTUBE_PREFIX = "v="
_URL_SCHEMES = ("https://", "http://")


class Platform(StrEnum):
    BILIBILI = "bilibili"
    TVER = "tver"
    ABEMA = "abema"
    YOUTUBE = "youtube"


_HOSTS = (
    ("bilibili.com", Platform.BILIBILI),
    ("youtube.com", Platform.YOUTUBE),
    ("youtu.be", Platform.YOUTUBE),
    ("tver.jp", Platform.TVER),
    ("abema.tv", Platform.ABEMA),
)


@dataclass(frozen=True, slots=True)
class SourceId:
    platform: Platform
    video_id: str

    @property
    def url(self) -> str:
        return source_url(self.platform, self.video_id)

    def __str__(self) -> str:
        return self.video_id


def parse_source(text: str) -> SourceId:
    """Parse a video ID or URL from any supported platform.

    A URL names its platform; a bare ID is classified by `platform_of`.
    Already-parsed IDs (including `v=<id>`) round-trip unchanged. Unknown
    URLs raise `ValueError`.
    """
    if text.startswith(_YOUTUBE_PREFIX):
        return SourceId(Platform.YOUTUBE, text)
    for host, platform in _HOSTS:
        if host in text:
            return SourceId(platform, _id_from_url(text, platform))
    if is_url(text):
        raise ValueError(f"Invalid video source: {text}")
    return SourceId(platform_of(text), text)


def is_url(text: str) -> bool:
    """Whether `text` is an http(s) URL rather than a bare ID or a path."""
    return text.startswith(_URL_SCHEMES)


def platform_of(video_id: str) -> Platform:
    """Infer the platform of a bare video ID.

    Ambiguous by nature: an Abema slot ID that happens to start with `ep`/`sh`
    reads as TVer, so projects persist the platform parsed from the URL.
    """
    if video_id.startswith("BV"):
        return Platform.BILIBILI
    if video_id.startswith(_YOUTUBE_PREFIX):
        return Platform.YOUTUBE
    # TVer episode/series IDs are purely alphanumeric; Abema is the fallback.
    if video_id.startswith(("ep", "sh")) and video_id.isalnum():
        return Platform.TVER
    return Platform.ABEMA


def source_url(platform: Platform, video_id: str) -> str:
    """The canonical watch URL yt-dlp is given for this video."""
    match platform:
        case Platform.BILIBILI:
            return f"https://www.bilibili.com/video/{video_id}"
        case Platform.TVER:
            return f"https://tver.jp/episodes/{video_id}"
        case Platform.ABEMA:
            # Live-archive slot IDs are pure alphanumeric; episode IDs always
            # contain `-`/`_`. yt-dlp never reads the slot URL's channel part.
            if video_id.isalnum():
                return f"https://abema.tv/channels/_/slots/{video_id}"
            return f"https://abema.tv/video/episode/{video_id}"
        case Platform.YOUTUBE:
            return f"https://www.youtube.com/watch?v={video_id.removeprefix(_YOUTUBE_PREFIX)}"


def _id_from_url(url: str, platform: Platform) -> str:
    parsed = urlparse(url)
    if platform is Platform.BILIBILI:
        if match := _BILIBILI_ID_RE.search(parsed.path):
            return match.group(1)
        raise ValueError(f"Invalid Bilibili URL: {url}")
    if platform is Platform.YOUTUBE and (query_ids := parse_qs(parsed.query).get("v")):
        return f"{_YOUTUBE_PREFIX}{query_ids[0]}"
    # youtu.be/<id>, /shorts/<id>, /episodes/<id>, /video/episode/<id>,
    # /channels/<ch>/slots/<id>
    last = parsed.path.strip("/").split("/")[-1]
    if not last:
        raise ValueError(f"Invalid {platform} URL: {url}")
    return f"{_YOUTUBE_PREFIX}{last}" if platform is Platform.YOUTUBE else last
