import json
import shutil
import tempfile
import unittest
from pathlib import Path

from services.package.rc import (
    load_package_rc,
    register_package_rc_program,
    resolve_remix_noise_name,
)


class PackageRcTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="packagerc-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        self.rc_path = root / ".packagerc"

    def _write_rc(self, payload: dict) -> None:
        self.rc_path.write_text(json.dumps(payload), encoding="utf-8")

    def _read_rc(self) -> dict:
        return json.loads(self.rc_path.read_text(encoding="utf-8"))

    def test_registers_new_names_with_empty_rules(self):
        register_package_rc_program(
            series="ドキュメンタル", channel="Prime Video", path=self.rc_path
        )

        self.assertEqual(
            self._read_rc(),
            {"series": {"ドキュメンタル": {}}, "channel": {"Prime Video": {}}},
        )

    def test_registering_keeps_existing_rules(self):
        self._write_rc({"series": {"ドキュメンタル": {"remix": True}}})

        register_package_rc_program(
            series="ドキュメンタル", channel="Prime Video", path=self.rc_path
        )

        self.assertEqual(
            self._read_rc(),
            {
                "series": {"ドキュメンタル": {"remix": True}},
                "channel": {"Prime Video": {}},
            },
        )

    def test_registering_nothing_new_leaves_the_file_alone(self):
        register_package_rc_program(
            series=None, channel=None, path=self.rc_path
        )

        self.assertFalse(self.rc_path.exists())

    def test_invalid_rules_are_never_overwritten(self):
        self.rc_path.write_text("{not json", encoding="utf-8")

        register_package_rc_program(
            series="ドキュメンタル", channel=None, path=self.rc_path
        )

        self.assertEqual(
            self.rc_path.read_text(encoding="utf-8"), "{not json"
        )
        self.assertEqual(load_package_rc(self.rc_path).series, {})

    def test_requested_noise_name_wins_over_rules(self):
        self._write_rc({"series": {"show": {"remix": True}}})

        self.assertEqual(
            resolve_remix_noise_name(
                requested="sleep",
                series="show",
                channel=None,
                path=self.rc_path,
            ),
            "sleep",
        )

    def test_series_rule_forces_the_default_noise_set(self):
        self._write_rc({"series": {"show": {"remix": True}}})

        self.assertEqual(
            resolve_remix_noise_name(
                requested=None,
                series="show",
                channel="station",
                path=self.rc_path,
            ),
            "default",
        )

    def test_channel_rule_forces_the_default_noise_set(self):
        self._write_rc({"channel": {"station": {"remix": True}}})

        self.assertEqual(
            resolve_remix_noise_name(
                requested=None,
                series="show",
                channel="station",
                path=self.rc_path,
            ),
            "default",
        )

    def test_listed_without_remix_stays_a_normal_package(self):
        self._write_rc(
            {"series": {"show": {}}, "channel": {"station": {"remix": False}}}
        )

        self.assertIsNone(
            resolve_remix_noise_name(
                requested=None,
                series="show",
                channel="station",
                path=self.rc_path,
            )
        )

    def test_unlisted_program_stays_a_normal_package(self):
        self.assertIsNone(
            resolve_remix_noise_name(
                requested=None,
                series="show",
                channel="station",
                path=self.rc_path,
            )
        )


if __name__ == "__main__":
    unittest.main()
