"""Resolve the announced broadcast/publication date for a video source.

The goal is the calendar date a show's official SNS announces ("○月○日放送/
公開") — platform-local wall-clock date, not a UTC date:

- YouTube: publish time (release time for premieres), JST.
- BiliBili: pubdate, CST (UTC+8) — announcements target a CST audience.
- TVer: the TV on-air date from `broadcastDateLabel` (usually month/day with
  no year; year inferred from the TVer availability start). Archive
  re-uploads instead carry a year-only label ("2018年放送"), where the
  availability start is years off the on-air date — those resolve to None so
  the agent research fallback takes over. Falls back to the availability date
  itself when the label is missing.
- ABEMA: `broadcastAt` for VOD episodes, `slot.startAt` for live-archive
  slots — both the original air time, JST.

Everything here is best-effort: any missing input yields None so the
metadata stage never blocks on a date.
"""

import re
from datetime import date, datetime, timedelta, timezone

from loguru import logger

from .info import (
    YtDlpVideoInfo,
    get_abema_episode_broadcast_at,
    get_abema_slot_start_at,
)

JST = timezone(timedelta(hours=9))
CST = timezone(timedelta(hours=8))

_FULL_DATE_PATTERN = re.compile(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")
_MONTH_DAY_PATTERN = re.compile(r"(\d{1,2})月(\d{1,2})日")
_YEAR_PATTERN = re.compile(r"(\d{4})年")


def date_from_epoch(epoch: int | None, tz: timezone) -> date | None:
    """Convert a Unix epoch to a wall-clock date in the given timezone."""
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz).date()


def nearest_date_for_month_day(
    month: int, day: int, reference: date
) -> date | None:
    """Pick the year that puts month/day closest to a reference date.

    TVer's broadcast label has no year. The availability start is at most
    days after (見逃し) or before (先行配信) the broadcast, so the nearest
    candidate across adjacent years is the right one — including across a
    year boundary. Invalid candidates (e.g. Feb 29 in a non-leap year) are
    skipped.
    """
    candidates = []
    for year in (reference.year - 1, reference.year, reference.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: abs(candidate - reference))


def parse_broadcast_label_year(label: str | None) -> int | None:
    """Extract the four-digit year a TVer broadcast label states, if any."""
    if not label:
        return None
    match = _YEAR_PATTERN.search(label)
    return int(match.group(1)) if match else None


def resolve_tver_broadcast_date(
    label: str | None, release_timestamp: int | None
) -> date | None:
    """Resolve a TVer on-air date from the label and availability start.

    A label that states its own year is authoritative: an explicit
    year/month/day is taken as-is, and a year-only archive label
    ("2018年放送") resolves to None rather than to the availability start,
    which for a re-upload is years away from the on-air date. Only a
    year-less month/day label may borrow the year from the availability
    start.
    """
    reference = date_from_epoch(release_timestamp, JST)
    if label:
        full_match = _FULL_DATE_PATTERN.search(label)
        if full_match:
            try:
                return date(*(int(group) for group in full_match.groups()))
            except ValueError:
                logger.warning(f"Invalid date in TVer label: {label}")

        month_day_match = _MONTH_DAY_PATTERN.search(label)
        if month_day_match and reference is not None:
            resolved = nearest_date_for_month_day(
                int(month_day_match.group(1)),
                int(month_day_match.group(2)),
                reference,
            )
            if resolved is not None:
                return resolved

        if _YEAR_PATTERN.search(label):
            logger.info(
                f"TVer label states a year but no on-air day ({label}); "
                "treating the availability start as unrelated to the "
                "broadcast date"
            )
            return None
    return reference


def _parse_upload_date(upload_date: str | None) -> date | None:
    """Parse yt-dlp's YYYYMMDD upload_date string."""
    if not upload_date:
        return None
    try:
        return datetime.strptime(upload_date, "%Y%m%d").date()
    except ValueError:
        return None


def resolve_broadcast_date(
    *,
    source: str,
    video_id: str,
    video_info: YtDlpVideoInfo,
    tver_broadcast_date_label: str | None = None,
) -> date | None:
    """Resolve the announced broadcast/publication date for a project.

    `source` is a `VideoSource` value ("youtube" / "bilibili" / "tver" /
    "abema"); it is typed as str to keep this service importable without
    the project module. ABEMA makes a best-effort platform API call; the
    TVer label is fetched and persisted by the metadata stage and passed in
    as `tver_broadcast_date_label`. Never raises: any unexpected failure
    degrades to None so the metadata stage stays unblocked.
    """
    try:
        return _resolve_broadcast_date(
            source=source,
            video_id=video_id,
            video_info=video_info,
            tver_broadcast_date_label=tver_broadcast_date_label,
        )
    except Exception as e:
        logger.warning(
            f"Broadcast date resolution failed for {source}/{video_id}: {e}"
        )
        return None


def _resolve_broadcast_date(
    *,
    source: str,
    video_id: str,
    video_info: YtDlpVideoInfo,
    tver_broadcast_date_label: str | None,
) -> date | None:
    if source == "bilibili":
        return date_from_epoch(video_info.timestamp, CST)

    if source == "youtube":
        epoch = video_info.release_timestamp or video_info.timestamp
        # upload_date is a last resort and UTC-dated, so a late-evening JST
        # publish can land one calendar day early.
        return date_from_epoch(epoch, JST) or _parse_upload_date(
            video_info.upload_date
        )

    if source == "tver":
        return resolve_tver_broadcast_date(
            tver_broadcast_date_label, video_info.release_timestamp
        )

    if source == "abema":
        # Pure-alphanumeric IDs are slots (live archives); episode IDs
        # always contain '-'/'_' — same rule as Project.source_url.
        if video_id.isalnum():
            epoch = get_abema_slot_start_at(video_id)
        else:
            epoch = get_abema_episode_broadcast_at(video_id)
        return date_from_epoch(epoch, JST)

    logger.warning(f"Unknown source for broadcast date resolution: {source}")
    return None
