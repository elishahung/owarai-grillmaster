from __future__ import annotations

from datetime import date

import pytest

from grillmaster.sources.broadcast_date import (
    CST,
    JST,
    date_from_epoch,
    nearest_date_for_month_day,
    parse_broadcast_label_year,
    parse_upload_date,
    resolve_tver_broadcast_date,
)

# 2026-07-08 22:00 JST
TVER_START_AT = 1783515600


def test_date_from_epoch_without_epoch():
    assert date_from_epoch(None, JST) is None


def test_date_from_epoch_uses_the_wall_clock_of_the_zone():
    # 2026-05-03 14:00 UTC = 23:00 JST, same date.
    assert date_from_epoch(1777816800, JST) == date(2026, 5, 3)


@pytest.mark.parametrize(
    ("epoch", "jst", "cst"),
    [
        # 16:30 UTC: past midnight in both zones.
        (1777825800, date(2026, 5, 4), date(2026, 5, 4)),
        # 15:30 UTC: 00:30 JST the next day, still 23:30 CST.
        (1777822200, date(2026, 5, 4), date(2026, 5, 3)),
    ],
)
def test_zones_shift_the_date_across_midnight(epoch: int, jst: date, cst: date):
    assert date_from_epoch(epoch, JST) == jst
    assert date_from_epoch(epoch, CST) == cst


@pytest.mark.parametrize(
    ("month", "day", "reference", "expected"),
    [
        (7, 8, date(2026, 7, 10), date(2026, 7, 8)),
        # December broadcast, availability in early January.
        (12, 28, date(2026, 1, 3), date(2025, 12, 28)),
        # Advance distribution (先行配信): the label date follows availability.
        (1, 2, date(2025, 12, 30), date(2026, 1, 2)),
        # Feb 29 only exists in the leap year.
        (2, 29, date(2024, 3, 5), date(2024, 2, 29)),
    ],
)
def test_nearest_date_for_month_day(
    month: int, day: int, reference: date, expected: date
):
    assert nearest_date_for_month_day(month, day, reference) == expected


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("7月6日(月)放送分", date(2026, 7, 6)),
        # A fully dated label is authoritative over the availability year.
        ("2018年8月22日放送", date(2018, 8, 22)),
        # Archive re-upload: availability is years off, research takes over.
        ("2018年放送", None),
        ("2018年8月放送", None),
        ("2018年2月30日放送", None),
        (None, date(2026, 7, 8)),
        ("放送日未定", date(2026, 7, 8)),
    ],
)
def test_resolve_tver_broadcast_date(label: str | None, expected: date | None):
    assert resolve_tver_broadcast_date(label, TVER_START_AT) == expected


def test_resolve_tver_broadcast_date_without_availability():
    assert resolve_tver_broadcast_date(None, None) is None
    assert resolve_tver_broadcast_date("7月6日(月)放送分", None) is None


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("2018年放送", 2018),
        ("2018年8月22日放送", 2018),
        ("8月26日(水)放送分", None),
        (None, None),
        ("", None),
    ],
)
def test_parse_broadcast_label_year(label: str | None, expected: int | None):
    assert parse_broadcast_label_year(label) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("20260503", date(2026, 5, 3)), ("2026-05-03", None), (None, None)],
)
def test_parse_upload_date(value: str | None, expected: date | None):
    assert parse_upload_date(value) == expected


@pytest.mark.parametrize("epoch", [10**20, -(10**20)])
def test_date_from_an_out_of_range_epoch_is_absent(epoch: int):
    assert date_from_epoch(epoch, JST) is None
