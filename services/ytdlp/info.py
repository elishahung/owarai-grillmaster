"""Video metadata extraction using yt-dlp.

This module provides functionality to extract video metadata without
downloading the actual video content.
"""

from .client import get_ytdlp_client_for_url
from typing import cast
from pydantic import BaseModel, Field
from loguru import logger
import re
import json
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class YtDlpVideoInfo(BaseModel):
    """Video metadata model extracted from yt-dlp.

    Attributes:
        id: The unique video identifier from the source platform.
        title: The video title.
        description: The video description text.
    """

    id: str
    title: str
    description: str | None = None

    @property
    def filename(self) -> str:
        return self.sanitize_filename(self.title)

    @staticmethod
    def sanitize_filename(text: str) -> str:
        """Sanitize a filename-safe version of the text."""
        safe_name = re.sub(r"[^\w\s-]", "", text).strip()
        safe_name = re.sub(r"[-\s]+", "_", safe_name)
        return safe_name


class SourceTalentInfo(BaseModel):
    """Normalized source talent metadata."""

    id: str
    name: str
    name_kana: str | None = None
    roles: list[str] = Field(default_factory=list)
    thumbnail_path: str | None = None


class TVerTalent(SourceTalentInfo):
    """Talent metadata returned by TVer's contents API."""


class AbemaTalent(SourceTalentInfo):
    """Talent metadata returned by ABEMA's program API."""


def _parse_tver_talents_response(data: dict) -> list[TVerTalent]:
    """Parse TVer contents API talent JSON into normalized talent records."""
    raw_talents = data.get("talents")
    if not isinstance(raw_talents, list):
        return []

    talents: list[TVerTalent] = []
    for raw_talent in raw_talents:
        if not isinstance(raw_talent, dict):
            continue
        roles = [
            role
            for role in (
                raw_talent.get("genre1"),
                raw_talent.get("genre2"),
                raw_talent.get("genre3"),
            )
            if isinstance(role, str) and role
        ]
        try:
            talents.append(
                TVerTalent(
                    id=raw_talent["id"],
                    name=raw_talent["name"],
                    name_kana=raw_talent.get("name_kana"),
                    roles=roles,
                    thumbnail_path=raw_talent.get("thumbnail_path"),
                )
            )
        except Exception as e:
            logger.warning(f"Skipping invalid TVer talent payload: {e}")
    return talents


def _parse_abema_casts_response(
    data: dict, episode_id: str
) -> list[AbemaTalent]:
    """Parse ABEMA program API credit casts into normalized talent records."""
    raw_casts = data.get("credit", {}).get("casts")
    if not isinstance(raw_casts, list):
        return []

    talents: list[AbemaTalent] = []
    current_role: str | None = None
    for raw_cast in raw_casts:
        if not isinstance(raw_cast, str):
            continue

        cast = raw_cast.strip()
        if not cast:
            continue

        if cast.startswith("■"):
            current_role = cast.lstrip("■").strip() or None
            continue

        talents.append(
            AbemaTalent(
                id=f"abema:{episode_id}:{len(talents) + 1}",
                name=cast,
                roles=[current_role] if current_role else [],
            )
        )
    return talents


def get_tver_episode_talents(episode_id: str) -> list[TVerTalent]:
    """Fetch episode talent metadata from TVer's contents API.

    This uses the same public contents endpoint consumed by TVer's web UI.
    It is treated as best-effort metadata: failures are logged and return an
    empty list so metadata fetching does not block the main workflow.
    """
    url = (
        "https://contents-api.tver.jp/contents/api/v1/episodes/"
        f"{episode_id}/talents"
    )
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Origin": "https://tver.jp",
            "Referer": "https://tver.jp/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/135.0.0.0 Safari/537.36"
            ),
            "x-tver-platform-type": "web",
        },
        method="GET",
    )
    try:
        logger.info(f"Fetching TVer talents for episode: {episode_id}")
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        talents = _parse_tver_talents_response(payload)
        logger.success(f"Fetched {len(talents)} TVer talents")
        return talents
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to fetch TVer talents for {episode_id}: {e}")
        return []


def _get_abema_device_token() -> str:
    """Create an anonymous ABEMA device token using yt-dlp's auth routine."""
    from yt_dlp.extractor.abematv import AbemaTVBaseIE

    device_id = str(uuid.uuid4())
    application_key_secret = AbemaTVBaseIE._generate_aks(device_id)
    request = Request(
        "https://api.abema.io/v1/users",
        data=json.dumps(
            {
                "deviceId": device_id,
                "applicationKeySecret": application_key_secret,
            }
        ).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Origin": "https://abema.tv",
            "Referer": "https://abema.tv/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/135.0.0.0 Safari/537.36"
            ),
        },
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        raise ValueError("ABEMA token response did not contain a token")
    return token


def get_abema_episode_talents(episode_id: str) -> list[AbemaTalent]:
    """Fetch episode cast metadata from ABEMA's program API.

    ABEMA exposes the page cast list under `credit.casts` in the same program
    API used by yt-dlp for episode metadata. The API requires an anonymous
    device token, so this reuses yt-dlp's current application-key routine.
    """
    url = f"https://api.abema.io/v1/video/programs/{episode_id}"
    try:
        logger.info(f"Fetching ABEMA talents for episode: {episode_id}")
        token = _get_abema_device_token()
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"bearer {token}",
                "Origin": "https://abema.tv",
                "Referer": "https://abema.tv/",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/135.0.0.0 Safari/537.36"
                ),
            },
            method="GET",
        )
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        talents = _parse_abema_casts_response(payload, episode_id)
        logger.success(f"Fetched {len(talents)} ABEMA talents")
        return talents
    except (
        HTTPError,
        URLError,
        TimeoutError,
        ValueError,
        json.JSONDecodeError,
    ) as e:
        logger.warning(f"Failed to fetch ABEMA talents for {episode_id}: {e}")
        return []


def get_video_info(input_str: str) -> YtDlpVideoInfo:
    """Extract video metadata without downloading.

    Uses yt-dlp to fetch video information including title, description,
    and other metadata from the source platform.

    Args:
        input_str: Video URL or identifier to extract info from.

    Returns:
        YtDlpVideoInfo containing the extracted metadata.

    Raises:
        Exception: If metadata extraction fails.
    """
    logger.info(f"Extracting video info for: {input_str}")
    try:
        with get_ytdlp_client_for_url(input_str) as ydl:
            info = ydl.extract_info(input_str, download=False)
            video_info = YtDlpVideoInfo.model_validate(cast(dict, info))
            logger.success(f"Successfully extracted info: {video_info.title}")
            return video_info
    except Exception as e:
        logger.error(f"Failed to extract video info for {input_str}: {e}")
        raise
