"""YouTube: publish date in JST, no extra platform metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from grillmaster.core.source_id import Platform
from grillmaster.sources.base import CookiePolicy, SourceExtras, SourcePlatform
from grillmaster.sources.broadcast_date import JST, date_from_epoch, parse_upload_date

if TYPE_CHECKING:
    from datetime import date

    from grillmaster.sources.http import JsonHttp
    from grillmaster.sources.ytdlp import VideoInfo


class YouTubePlatform(SourcePlatform):
    platform = Platform.YOUTUBE
    cookie_policy = CookiePolicy.CONFIGURED
    describes_program = True

    @override
    def fetch_extras(self, video_id: str, http: JsonHttp) -> SourceExtras:
        return SourceExtras()

    @override
    def resolve_broadcast_date(
        self, info: VideoInfo, extras: SourceExtras
    ) -> date | None:
        # Release time covers premieres. `upload_date` is a last resort and
        # UTC-dated, so a late-evening JST publish can land a day early.
        epoch = info.release_timestamp or info.timestamp
        return date_from_epoch(epoch, JST) or parse_upload_date(info.upload_date)
