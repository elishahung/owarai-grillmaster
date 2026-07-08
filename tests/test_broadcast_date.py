import unittest
from datetime import date
from unittest.mock import patch

import services.ytdlp.broadcast_date as broadcast_date_module
from services.ytdlp.broadcast_date import (
    CST,
    JST,
    date_from_epoch,
    nearest_date_for_month_day,
    resolve_broadcast_date,
    resolve_tver_broadcast_date,
)
from services.ytdlp.info import YtDlpVideoInfo


def _video_info(**kwargs) -> YtDlpVideoInfo:
    return YtDlpVideoInfo(id="demo", title="demo title", **kwargs)


class DateFromEpochTests(unittest.TestCase):
    def test_none_epoch_returns_none(self):
        self.assertIsNone(date_from_epoch(None, JST))

    def test_jst_wall_clock_date(self):
        # 2026-05-03 14:00 UTC = 2026-05-03 23:00 JST (same date)
        self.assertEqual(
            date_from_epoch(1777816800, JST), date(2026, 5, 3)
        )

    def test_timezone_shifts_date_across_midnight(self):
        # 2026-05-03 16:30 UTC = 2026-05-04 01:30 JST but 2026-05-04 00:30 CST
        epoch = 1777825800
        self.assertEqual(date_from_epoch(epoch, JST), date(2026, 5, 4))
        self.assertEqual(date_from_epoch(epoch, CST), date(2026, 5, 4))
        # 2026-05-03 15:30 UTC = 2026-05-04 00:30 JST but 2026-05-03 23:30 CST
        epoch = 1777822200
        self.assertEqual(date_from_epoch(epoch, JST), date(2026, 5, 4))
        self.assertEqual(date_from_epoch(epoch, CST), date(2026, 5, 3))


class NearestDateForMonthDayTests(unittest.TestCase):
    def test_same_year(self):
        self.assertEqual(
            nearest_date_for_month_day(7, 8, date(2026, 7, 10)),
            date(2026, 7, 8),
        )

    def test_year_boundary_backward(self):
        # December broadcast, availability start in early January.
        self.assertEqual(
            nearest_date_for_month_day(12, 28, date(2026, 1, 3)),
            date(2025, 12, 28),
        )

    def test_year_boundary_forward(self):
        # Advance distribution (先行配信): label date after availability.
        self.assertEqual(
            nearest_date_for_month_day(1, 2, date(2025, 12, 30)),
            date(2026, 1, 2),
        )

    def test_invalid_feb_29_skips_non_leap_years(self):
        self.assertEqual(
            nearest_date_for_month_day(2, 29, date(2024, 3, 5)),
            date(2024, 2, 29),
        )


class ResolveTverBroadcastDateTests(unittest.TestCase):
    # 2026-07-08 22:00 JST
    _START_AT = 1783515600

    def test_label_month_day_with_year_inference(self):
        self.assertEqual(
            resolve_tver_broadcast_date("7月6日(月)放送分", self._START_AT),
            date(2026, 7, 6),
        )

    def test_missing_label_falls_back_to_availability_date(self):
        self.assertEqual(
            resolve_tver_broadcast_date(None, self._START_AT),
            date(2026, 7, 8),
        )

    def test_unparseable_label_falls_back_to_availability_date(self):
        self.assertEqual(
            resolve_tver_broadcast_date("放送日未定", self._START_AT),
            date(2026, 7, 8),
        )

    def test_no_inputs_returns_none(self):
        self.assertIsNone(resolve_tver_broadcast_date(None, None))
        self.assertIsNone(resolve_tver_broadcast_date("7月6日(月)放送分", None))


class ResolveBroadcastDateTests(unittest.TestCase):
    def test_bilibili_uses_pubdate_in_cst(self):
        # 2026-05-03 15:30 UTC = 23:30 CST May 3 (but 00:30 JST May 4)
        result = resolve_broadcast_date(
            source="bilibili",
            video_id="BV1demo",
            video_info=_video_info(timestamp=1777822200),
        )
        self.assertEqual(result, date(2026, 5, 3))

    def test_youtube_prefers_release_timestamp(self):
        result = resolve_broadcast_date(
            source="youtube",
            video_id="v=demo",
            video_info=_video_info(
                timestamp=1777816800,
                release_timestamp=1777903200,  # premiere a day later
            ),
        )
        self.assertEqual(result, date(2026, 5, 4))

    def test_youtube_falls_back_to_upload_date(self):
        result = resolve_broadcast_date(
            source="youtube",
            video_id="v=demo",
            video_info=_video_info(upload_date="20260503"),
        )
        self.assertEqual(result, date(2026, 5, 3))

    def test_tver_combines_label_and_release_timestamp(self):
        with patch.object(
            broadcast_date_module,
            "get_tver_broadcast_date_label",
            return_value="7月6日(月)放送分",
        ) as get_label:
            result = resolve_broadcast_date(
                source="tver",
                video_id="epdemo1",
                video_info=_video_info(release_timestamp=1783515600),
            )
        get_label.assert_called_once_with("epdemo1")
        self.assertEqual(result, date(2026, 7, 6))

    def test_abema_episode_uses_broadcast_at(self):
        with patch.object(
            broadcast_date_module,
            "get_abema_episode_broadcast_at",
            return_value=1777816800,  # 2026-05-03 23:00 JST
        ) as get_broadcast_at:
            result = resolve_broadcast_date(
                source="abema",
                video_id="90-979_s1_p360",
                video_info=_video_info(),
            )
        get_broadcast_at.assert_called_once_with("90-979_s1_p360")
        self.assertEqual(result, date(2026, 5, 3))

    def test_abema_slot_uses_slot_start_at(self):
        with patch.object(
            broadcast_date_module,
            "get_abema_slot_start_at",
            return_value=1783346400,  # 2026-07-06 23:00 JST
        ) as get_start_at:
            result = resolve_broadcast_date(
                source="abema",
                video_id="DGzv6KEKhRHpe3",
                video_info=_video_info(),
            )
        get_start_at.assert_called_once_with("DGzv6KEKhRHpe3")
        self.assertEqual(result, date(2026, 7, 6))

    def test_unknown_source_returns_none(self):
        self.assertIsNone(
            resolve_broadcast_date(
                source="niconico",
                video_id="sm1",
                video_info=_video_info(),
            )
        )


if __name__ == "__main__":
    unittest.main()
