"""ABEMA: cast and original air time from ABEMA's API, and the token reset.

Video IDs come in two shapes (`core.source_id.source_url`): episode IDs
always contain `-`/`_`, live-archive slot IDs are purely alphanumeric. An
episode's program API carries the cast (`credit.casts`) and `broadcastAt`;
a slot's API carries `slot.startAt`. Both times are the original on-air
time. The API wants an anonymous device token, minted with yt-dlp's own
application-key routine.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any, override

from loguru import logger

from grillmaster.core.source_id import Platform
from grillmaster.core.talent import Talent
from grillmaster.sources.base import (
    CookiePolicy,
    SourceExtras,
    SourcePlatform,
)
from grillmaster.sources.broadcast_date import JST, date_from_epoch
from grillmaster.sources.errors import SourceHttpError
from grillmaster.sources.http import BROWSER_USER_AGENT

if TYPE_CHECKING:
    from datetime import date

    from grillmaster.sources.http import JsonHttp
    from grillmaster.sources.ytdlp import VideoInfo

USERS_URL = "https://api.abema.io/v1/users"
PROGRAM_URL = "https://api.abema.io/v1/video/programs/{id}"
SLOT_URL = "https://api.abema.io/v1/media/slots/{id}"
_ROLE_MARK = "■"
_BASE_HEADERS = {
    "Origin": "https://abema.tv",
    "Referer": "https://abema.tv/",
    "User-Agent": BROWSER_USER_AGENT,
}


def is_slot(video_id: str) -> bool:
    return video_id.isalnum()


class AbemaPlatform(SourcePlatform):
    platform = Platform.ABEMA
    cookie_policy = CookiePolicy.CONFIGURED
    describes_program = True

    @override
    def fetch_extras(self, video_id: str, http: JsonHttp) -> SourceExtras:
        if is_slot(video_id):
            payload = _get_json(SLOT_URL.format(id=video_id), http)
            slot = payload.get("slot") if isinstance(payload, dict) else None
            start_at = slot.get("startAt") if isinstance(slot, dict) else None
            return SourceExtras(on_air_epoch=_epoch(start_at))
        payload = _get_json(PROGRAM_URL.format(id=video_id), http)
        if not isinstance(payload, dict):
            return SourceExtras()
        talents = parse_casts(payload, video_id)
        logger.info(f"Fetched {len(talents)} ABEMA talents")
        return SourceExtras(
            talents=talents, on_air_epoch=_epoch(payload.get("broadcastAt"))
        )

    @override
    def resolve_broadcast_date(
        self, info: VideoInfo, extras: SourceExtras
    ) -> date | None:
        return date_from_epoch(extras.on_air_epoch, JST)

    @override
    def before_download(self) -> None:
        reset_auth_cache()


def reset_auth_cache() -> None:
    """Drop yt-dlp's process-wide ABEMA token cache before a download.

    `AbemaTVBaseIE` caches the user token, device ID and media token as class
    attributes, and only the branch minting a fresh token registers the
    `abematv-license://` handler, on the `YoutubeDL` current at that moment.
    After the metadata stage authorized in this process, the download's own
    `YoutubeDL` would take the cached-token early return, never get the
    license handler, and fail every HLS key fetch. Clearing the cache makes
    the download re-authorize, as a fresh process would.
    """
    from yt_dlp.extractor.abematv import AbemaTVBaseIE  # noqa: PLC0415 - heavy

    AbemaTVBaseIE._USERTOKEN = None  # noqa: SLF001 - yt-dlp keeps no public reset
    AbemaTVBaseIE._DEVICE_ID = None  # noqa: SLF001
    AbemaTVBaseIE._MEDIATOKEN = None  # noqa: SLF001
    logger.debug("Cleared the cached ABEMA device token before download")


def parse_casts(payload: dict[str, Any], episode_id: str) -> tuple[Talent, ...]:
    """Talents from `credit.casts`, where `■<role>` lines head each group."""
    credit = payload.get("credit")
    raw_casts = credit.get("casts") if isinstance(credit, dict) else None
    if not isinstance(raw_casts, list):
        return ()
    talents: list[Talent] = []
    role: str | None = None
    for raw in raw_casts:
        if not isinstance(raw, str) or not (cast := raw.strip()):
            continue
        if cast.startswith(_ROLE_MARK):
            role = cast.lstrip(_ROLE_MARK).strip() or None
            continue
        talents.append(
            Talent(
                id=f"abema:{episode_id}:{len(talents) + 1}",
                name=cast,
                roles=(role,) if role else (),
            )
        )
    return tuple(talents)


def _get_json(url: str, http: JsonHttp) -> Any:
    """GET an API endpoint with a fresh anonymous token; `None` on failure."""
    try:
        token = _device_token(http)
        return http.request(
            url,
            headers={
                **_BASE_HEADERS,
                "Accept": "application/json",
                "Authorization": f"bearer {token}",
            },
        )
    except SourceHttpError as error:
        logger.warning(f"ABEMA API request failed: {error}")
        return None


def _device_token(http: JsonHttp) -> str:
    from yt_dlp.extractor.abematv import AbemaTVBaseIE  # noqa: PLC0415 - heavy

    device_id = str(uuid.uuid4())
    body = json.dumps(
        {
            "deviceId": device_id,
            "applicationKeySecret": AbemaTVBaseIE._generate_aks(device_id),  # noqa: SLF001 - yt-dlp's key routine
        }
    ).encode("utf-8")
    payload = http.request(
        USERS_URL,
        headers={**_BASE_HEADERS, "Content-Type": "application/json"},
        data=body,
    )
    token = payload.get("token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise SourceHttpError("ABEMA token response did not contain a token")
    return token


def _epoch(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
