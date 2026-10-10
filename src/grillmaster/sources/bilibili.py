"""BiliBili: anonymous downloads, pubdate in CST, title-only source text."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from grillmaster.core.source_id import Platform
from grillmaster.sources.base import CookiePolicy, SourceExtras, SourcePlatform
from grillmaster.sources.broadcast_date import CST, date_from_epoch

if TYPE_CHECKING:
    from datetime import date

    from grillmaster.sources.http import JsonHttp
    from grillmaster.sources.ytdlp import VideoInfo


class BilibiliPlatform(SourcePlatform):
    platform = Platform.BILIBILI
    # Signed-in cookies can restrict the format list (see `ytdlp.shared_options`).
    cookie_policy = CookiePolicy.ANONYMOUS
    # Descriptions are uploader notes, not program information.
    describes_program = False

    @override
    def fetch_extras(self, video_id: str, http: JsonHttp) -> SourceExtras:
        return SourceExtras()

    @override
    def resolve_broadcast_date(
        self, info: VideoInfo, extras: SourceExtras
    ) -> date | None:
        # Re-uploads are announced to a CST audience.
        return date_from_epoch(info.timestamp, CST)
