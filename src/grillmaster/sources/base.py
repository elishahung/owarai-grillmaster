"""What every source platform provides: the `SourcePlatform` protocol.

A platform turns a video ID into the URL yt-dlp is given, says whether
downloads send the configured cookies, fetches the metadata yt-dlp does not
read (talents, the raw on-air label, the original air time) and resolves the
broadcast date announcements use. Extras and dates are best-effort: a
platform API failure is logged and yields nothing, so missing metadata never
blocks a run.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from grillmaster.core.source_id import source_url

if TYPE_CHECKING:
    from datetime import date

    from grillmaster.core.source_id import Platform
    from grillmaster.sources.http import JsonHttp
    from grillmaster.sources.ytdlp import VideoInfo


class CookiePolicy(StrEnum):
    # Send `paths.cookies` (TVer/ABEMA/YouTube may need a signed-in session).
    CONFIGURED = "configured"
    # Never send cookies: some accounts get a restricted format list.
    ANONYMOUS = "anonymous"


@dataclass(frozen=True, slots=True)
class SourceTalent:
    """A person or group the platform credits for the program."""

    id: str
    name: str
    name_kana: str | None = None
    roles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceExtras:
    """Platform metadata beyond yt-dlp's info.

    `broadcast_label` is the raw on-air label (e.g. "7月8日(火)放送分",
    "2018年放送"); `on_air_epoch` is an original air time the platform API
    reported (Unix seconds).
    """

    talents: tuple[SourceTalent, ...] = ()
    broadcast_label: str | None = None
    on_air_epoch: int | None = None


class SourcePlatform(Protocol):
    """Implementations subclass this protocol explicitly (one instance each)."""

    platform: Platform
    cookie_policy: CookiePolicy
    # Whether yt-dlp's description is program information rather than
    # uploader notes.
    describes_program: bool

    def url(self, video_id: str) -> str:
        """The watch URL yt-dlp is given."""
        return source_url(self.platform, video_id)

    def fetch_extras(self, video_id: str, http: JsonHttp) -> SourceExtras:
        """Talents, on-air label and air time; never raises."""
        ...

    def resolve_broadcast_date(
        self, info: VideoInfo, extras: SourceExtras
    ) -> date | None:
        """The platform-local date the show was announced as aired/published."""
        ...

    def before_download(self) -> None:
        """Prepare yt-dlp's process-wide state for a download (default: nothing)."""
        return
