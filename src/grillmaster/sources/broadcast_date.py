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

# Japanese weekday initials in `date.weekday()` order.
_WEEKDAYS = "月火水木金土日"
_FULL_DATE_PATTERN = re.compile(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日")
# "2月23日(月)放送分", "2月23日（月・祝）"; the weekday is optional.
_MONTH_DAY_PATTERN = re.compile(
    rf"(\d{{1,2}})月(\d{{1,2}})日(?:\s*[(（]([{_WEEKDAYS}]))?"
)
_YEAR_PATTERN = re.compile(r"(\d{4})年")
# A generous bound on how early 先行配信 opens an episode (days in practice).
_ADVANCE_RELEASE = timedelta(days=30)


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


def date_for_month_day(
    month: int, day: int, weekday: int | None, availability: date
) -> date | None:
    """The on-air date a year-less TVer label means.

    The availability start bounds the broadcast from one side only: advance
    distribution (先行配信) opens an episode at most `_ADVANCE_RELEASE`
    before it airs, while a rerun or re-listing opens it any time after (a
    February episode re-listed with its original label in October). So the
    answer is the latest month/day no later than that bound. A stated
    `weekday` (0 = Monday) must agree with it: a mismatch means the episode
    is older than the label can tell, so the year is left to research
    (`None`). Invalid dates (Feb 29 in a non-leap year) are skipped.
    """
    latest = availability + _ADVANCE_RELEASE
    candidates: list[date] = []
    for year in (latest.year - 1, latest.year):
        try:
            candidates.append(date(year, month, day))
        except ValueError:
            continue
    aired = max(
        (candidate for candidate in candidates if candidate <= latest), default=None
    )
    if aired is None or weekday in (None, aired.weekday()):
        return aired
    return None


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
    which for a re-upload is years away from the on-air date. A year-less
    month/day label is dated by `date_for_month_day`; only without a usable
    label is the availability date itself the answer.
    """
    availability = date_from_epoch(release_timestamp, JST)
    if not label:
        return availability
    if full_match := _FULL_DATE_PATTERN.search(label):
        try:
            return date(*(int(group) for group in full_match.groups()))
        except ValueError:
            logger.warning(f"Invalid date in TVer label: {label}")
    if _YEAR_PATTERN.search(label):
        logger.info(
            f"TVer label states a year but no on-air day ({label}); the "
            "availability start is unrelated to the broadcast date"
        )
        return None
    if month_day_match := _MONTH_DAY_PATTERN.search(label):
        if availability is None:
            return None
        month, day, weekday = month_day_match.groups()
        aired = date_for_month_day(
            int(month),
            int(day),
            _WEEKDAYS.index(weekday) if weekday else None,
            availability,
        )
        if aired is None:
            logger.info(
                f"TVer label {label} names no date the availability start "
                f"({availability:%Y-%m-%d}) can confirm; left to research"
            )
        return aired
    return availability


def parse_upload_date(upload_date: str | None) -> date | None:
    """yt-dlp's `YYYYMMDD` upload date (a UTC date)."""
    if not upload_date:
        return None
    try:
        return datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=UTC).date()
    except ValueError:
        return None
