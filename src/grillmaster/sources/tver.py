"""TVer: talents and the on-air label from TVer's web APIs.

Both endpoints are the public ones TVer's web UI calls. The on-air label
(放送日) is usually month/day with no year ("7月8日(火)放送分"); archive
re-uploads carry a year-only label ("2018年放送") instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, override
from urllib.parse import urlencode

from loguru import logger

from grillmaster.core.source_id import Platform
from grillmaster.core.talent import Talent
from grillmaster.sources.base import (
    CookiePolicy,
    SourceExtras,
    SourcePlatform,
)
from grillmaster.sources.broadcast_date import resolve_tver_broadcast_date
from grillmaster.sources.errors import SourceHttpError
from grillmaster.sources.http import BROWSER_USER_AGENT

if TYPE_CHECKING:
    from datetime import date

    from grillmaster.sources.http import JsonHttp
    from grillmaster.sources.ytdlp import VideoInfo

_WEB_HEADERS = {
    "Accept": "application/json",
    "Origin": "https://tver.jp",
    "Referer": "https://tver.jp/",
    "User-Agent": BROWSER_USER_AGENT,
    "x-tver-platform-type": "web",
}
TALENTS_URL = "https://contents-api.tver.jp/contents/api/v1/episodes/{id}/talents"
SESSION_URL = "https://platform-api.tver.jp/v2/api/platform_users/browser/create"
EPISODE_URL = "https://platform-api.tver.jp/service/api/v1/callEpisode/{id}"
_ROLE_FIELDS = ("genre1", "genre2", "genre3")


class TverPlatform(SourcePlatform):
    platform = Platform.TVER
    cookie_policy = CookiePolicy.CONFIGURED
    describes_program = True

    @override
    def fetch_extras(self, video_id: str, http: JsonHttp) -> SourceExtras:
        return SourceExtras(
            talents=fetch_talents(video_id, http),
            broadcast_label=fetch_broadcast_label(video_id, http),
        )

    @override
    def resolve_broadcast_date(
        self, info: VideoInfo, extras: SourceExtras
    ) -> date | None:
        return resolve_tver_broadcast_date(
            extras.broadcast_label, info.release_timestamp
        )


def fetch_talents(episode_id: str, http: JsonHttp) -> tuple[Talent, ...]:
    """Episode talents; empty when the API fails."""
    try:
        payload = http.request(TALENTS_URL.format(id=episode_id), headers=_WEB_HEADERS)
    except SourceHttpError as error:
        logger.warning(f"Failed to fetch TVer talents for {episode_id}: {error}")
        return ()
    talents = parse_talents(payload)
    logger.info(f"Fetched {len(talents)} TVer talents")
    return talents


def parse_talents(payload: object) -> tuple[Talent, ...]:
    """Talents from the contents API; malformed entries are skipped."""
    raw_talents = payload.get("talents") if isinstance(payload, dict) else None
    if not isinstance(raw_talents, list):
        return ()
    talents: list[Talent] = []
    for raw in raw_talents:
        if not isinstance(raw, dict):
            continue
        talent_id, name, kana = raw.get("id"), raw.get("name"), raw.get("name_kana")
        if not isinstance(talent_id, str) or not isinstance(name, str):
            logger.warning(f"Skipping invalid TVer talent payload: {raw}")
            continue
        roles = tuple(
            role
            for role in (raw.get(field) for field in _ROLE_FIELDS)
            if isinstance(role, str) and role
        )
        talents.append(
            Talent(
                id=talent_id,
                name=name,
                name_kana=kana if isinstance(kana, str) else None,
                roles=roles,
            )
        )
    return tuple(talents)


def fetch_broadcast_label(episode_id: str, http: JsonHttp) -> str | None:
    """The on-air label (放送日); `None` when empty or the API fails.

    Empty for some TVer-original and special content.
    """
    try:
        session = http.request(
            SESSION_URL, headers=_WEB_HEADERS, data=b"device_type=pc"
        )
        result = session["result"]
        query = urlencode(
            {
                "platform_uid": result["platform_uid"],
                "platform_token": result["platform_token"],
            }
        )
        payload = http.request(
            f"{EPISODE_URL.format(id=episode_id)}?{query}", headers=_WEB_HEADERS
        )
        label = payload["result"]["episode"]["content"].get("broadcastDateLabel")
    except (SourceHttpError, KeyError, TypeError, AttributeError) as error:
        logger.warning(
            f"Failed to fetch the TVer on-air label for {episode_id}: {error}"
        )
        return None
    if not isinstance(label, str) or not label.strip():
        logger.info(f"TVer episode {episode_id} has no on-air label")
        return None
    logger.info(f"TVer on-air label: {label}")
    return label
