import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import services.ytdlp.info as info_module
from services.ytdlp.info import (
    _parse_abema_casts_response,
    _parse_tver_talents_response,
    get_abema_episode_broadcast_at,
    get_abema_slot_start_at,
    get_tver_broadcast_date_label,
)


def _mock_json_response(payload: dict) -> MagicMock:
    """Build a context-manager mock mimicking urlopen's response."""
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    manager = MagicMock()
    manager.__enter__.return_value = response
    manager.__exit__.return_value = False
    return manager


class YtDlpInfoTests(unittest.TestCase):
    def test_parse_tver_talents_response_normalizes_roles(self):
        talents = _parse_tver_talents_response(
            {
                "talents": [
                    {
                        "id": "t001",
                        "name": "小栗　有以",
                        "name_kana": "オグリ　ユイ",
                        "genre1": "アイドル",
                        "genre2": "",
                        "genre3": "俳優",
                        "thumbnail_path": "/images/t001.jpg",
                    }
                ]
            }
        )

        self.assertEqual(len(talents), 1)
        self.assertEqual(talents[0].id, "t001")
        self.assertEqual(talents[0].name, "小栗　有以")
        self.assertEqual(talents[0].roles, ["アイドル", "俳優"])

    def test_parse_abema_casts_response_assigns_section_roles(self):
        talents = _parse_abema_casts_response(
            {
                "credit": {
                    "casts": [
                        "■MC",
                        "千鳥",
                        "■ゲスト",
                        "渡部健（アンジャッシュ）",
                    ]
                }
            },
            "90-979_s1_p359",
        )

        self.assertEqual(len(talents), 2)
        self.assertEqual(talents[0].name, "千鳥")
        self.assertEqual(talents[0].roles, ["MC"])
        self.assertEqual(talents[1].name, "渡部健（アンジャッシュ）")
        self.assertEqual(talents[1].roles, ["ゲスト"])


class TverBroadcastDateLabelTests(unittest.TestCase):
    _SESSION = {
        "result": {"platform_uid": "uid", "platform_token": "token"}
    }

    def _episode(self, label) -> dict:
        return {"result": {"episode": {"content": {"broadcastDateLabel": label}}}}

    def test_returns_label(self):
        with patch.object(
            info_module,
            "urlopen",
            side_effect=[
                _mock_json_response(self._SESSION),
                _mock_json_response(self._episode("7月6日(月)放送分")),
            ],
        ):
            label = get_tver_broadcast_date_label("epdemo1")
        self.assertEqual(label, "7月6日(月)放送分")

    def test_empty_label_returns_none(self):
        with patch.object(
            info_module,
            "urlopen",
            side_effect=[
                _mock_json_response(self._SESSION),
                _mock_json_response(self._episode("")),
            ],
        ):
            self.assertIsNone(get_tver_broadcast_date_label("epdemo1"))

    def test_null_session_result_returns_none(self):
        with patch.object(
            info_module,
            "urlopen",
            side_effect=[_mock_json_response({"result": None})],
        ):
            self.assertIsNone(get_tver_broadcast_date_label("epdemo1"))

    def test_network_error_returns_none(self):
        with patch.object(
            info_module, "urlopen", side_effect=URLError("boom")
        ):
            self.assertIsNone(get_tver_broadcast_date_label("epdemo1"))


class AbemaBroadcastEpochTests(unittest.TestCase):
    def test_episode_broadcast_at_returns_epoch(self):
        with patch.object(
            info_module,
            "_get_abema_api_json",
            return_value={"broadcastAt": 1777816800},
        ):
            self.assertEqual(
                get_abema_episode_broadcast_at("90-979_s1_p360"), 1777816800
            )

    def test_episode_broadcast_at_non_int_returns_none(self):
        with patch.object(
            info_module,
            "_get_abema_api_json",
            return_value={"broadcastAt": None},
        ):
            self.assertIsNone(get_abema_episode_broadcast_at("90-979_s1_p360"))

    def test_slot_start_at_returns_epoch(self):
        with patch.object(
            info_module,
            "_get_abema_api_json",
            return_value={"slot": {"startAt": 1783346400}},
        ):
            self.assertEqual(
                get_abema_slot_start_at("DGzv6KEKhRHpe3"), 1783346400
            )

    def test_slot_start_at_null_slot_returns_none(self):
        with patch.object(
            info_module,
            "_get_abema_api_json",
            return_value={"slot": None},
        ):
            self.assertIsNone(get_abema_slot_start_at("DGzv6KEKhRHpe3"))

    def test_slot_start_at_error_returns_none(self):
        with patch.object(
            info_module,
            "_get_abema_api_json",
            side_effect=URLError("boom"),
        ):
            self.assertIsNone(get_abema_slot_start_at("DGzv6KEKhRHpe3"))


class YtDlpVideoInfoEpochCoercionTests(unittest.TestCase):
    def test_fractional_float_truncates(self):
        info = info_module.YtDlpVideoInfo.model_validate(
            {"id": "x", "title": "t", "timestamp": 1777816800.5}
        )
        self.assertEqual(info.timestamp, 1777816800)

    def test_non_numeric_degrades_to_none(self):
        info = info_module.YtDlpVideoInfo.model_validate(
            {"id": "x", "title": "t", "release_timestamp": "soon"}
        )
        self.assertIsNone(info.release_timestamp)


if __name__ == "__main__":
    unittest.main()
