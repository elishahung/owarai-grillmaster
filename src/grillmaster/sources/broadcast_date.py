"""Broadcast-date building blocks the platforms share.

The goal is the calendar date a show's official SNS announces ("○月○日放送/
公開"): the platform-local wall-clock date, not a UTC date. Each platform
module picks its inputs (YouTube publish time, BiliBili pubdate, the TVer
on-air label, the ABEMA original air time); the parsing lives here.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta, timezone

from loguru import logger

JST = timezone(timedelta(hours=9))
CST = timezone(timedelta(hours=8))

_FULL_DATE_PATTERN = re.compile(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")
_MONTH_DAY_PATTERN = re.compile(r"(\d{1,2})月(\d{1,2})日")
_YEAR_PATTERN = re.compile(r"(\d{4})年")


def date_from_epoch(epoch: int | None, tz: timezone) -> date | None:
    """The wall-clock date of a Unix epoch in `tz`; `None` (with a warning)
    for an epoch the platform could not have meant (out of range)."""
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(epoch, tz).date()
    except (OSError, OverflowError, ValueError) as error:
        logger.warning(f"Ignoring unusable epoch {epoch}: {error}")
        return None


def nearest_date_for_month_day(month: int, day: int, reference: date) -> date | None:
    """The year that puts month/day closest to `reference`.

    TVer's on-air label has no year. The availability start is at most days
    after (見逃し) or before (先行配信) the broadcast, so the nearest candidate
    across adjacent years is the right one, including across a year boundary.
    Invalid candidates (Feb 29 in a non-leap year) are skipped.
    """
    candidates: list[date] = []
    for year in (reference.year - 1, reference.year, reference.year + 1):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: abs(candidate - reference))


def parse_broadcast_label_year(label: str | None) -> int | None:
    """The four-digit year an on-air label states, if any."""
    if not label:
        return None
    match = _YEAR_PATTERN.search(label)
    return int(match.group(1)) if match else None


def resolve_tver_broadcast_date(
    label: str | None, release_timestamp: int | None
) -> date | None:
    """A TVer on-air date from the label and the availability start.

    A label stating its own year is authoritative: an explicit
    year/month/day is taken as-is, and a year-only archive label
    ("2018年放送") resolves to `None` rather than to the availability start,
    which for a re-upload is years away from the on-air date. Only a
    year-less month/day label borrows the year from the availability start;
    without a usable label the availability date itself is the answer.
    """
    reference = date_from_epoch(release_timestamp, JST)
    if not label:
        return reference
    if full_match := _FULL_DATE_PATTERN.search(label):
        try:
            return date(*(int(group) for group in full_match.groups()))
        except ValueError:
            logger.warning(f"Invalid date in TVer label: {label}")
    month_day_match = _MONTH_DAY_PATTERN.search(label)
    if month_day_match and reference is not None:
        resolved = nearest_date_for_month_day(
            int(month_day_match.group(1)), int(month_day_match.group(2)), reference
        )
        if resolved is not None:
            return resolved
    if _YEAR_PATTERN.search(label):
        logger.info(
            f"TVer label states a year but no on-air day ({label}); the "
            "availability start is unrelated to the broadcast date"
        )
        return None
    return reference


def parse_upload_date(upload_date: str | None) -> date | None:
    """yt-dlp's `YYYYMMDD` upload date (a UTC date)."""
    if not upload_date:
        return None
    try:
        return datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=UTC).date()
    except ValueError:
        return None
