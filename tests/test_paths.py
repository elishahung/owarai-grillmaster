import unittest
from pathlib import Path
from unittest.mock import patch

import services.paths as paths_module
from services.paths import fit_dir_name, measure


class FitDirNameTests(unittest.TestCase):
    def _fit(self, *, parent: str, tail: str, reserve: int = 20) -> str:
        return fit_dir_name(
            parent=Path(parent),
            keep="260503_ep123",
            tail=tail,
            reserve=reserve,
        )

    def test_short_name_is_returned_unchanged(self):
        with patch.object(paths_module, "MAX_PATH_UNITS", 259):
            self.assertEqual(
                self._fit(parent="C:/archive/26/05", tail="demo_show"),
                "260503_ep123_demo_show",
            )

    def test_long_name_is_trimmed_to_the_remaining_budget(self):
        parent = "C:/archive/26/05"
        with patch.object(paths_module, "MAX_PATH_UNITS", 100):
            fitted = self._fit(parent=parent, tail="t" * 200)

        self.assertTrue(fitted.startswith("260503_ep123_t"))
        self.assertEqual(len(parent) + 1 + len(fitted) + 20, 100)

    def test_component_limit_applies_even_with_a_short_parent(self):
        with patch.object(paths_module, "MAX_PATH_UNITS", 4096):
            fitted = self._fit(parent="C:/a", tail="t" * 400)

        self.assertEqual(len(fitted), paths_module.MAX_COMPONENT_UNITS)

    def test_trailing_separator_characters_are_stripped(self):
        # The budget cuts mid-name right after an underscore; the result must
        # not look like a dangling fragment.
        parent = "C:/archive/26/05"
        with patch.object(paths_module, "MAX_PATH_UNITS", 100):
            fitted = self._fit(parent=parent, tail="a" * 50 + "___" + "b" * 50)

        self.assertFalse(fitted.endswith("_"))

    def test_identity_prefix_survives_when_nothing_else_fits(self):
        parent = "C:/deep"
        limit = len(str(Path(parent).absolute())) + 1 + len("260503_ep123") + 20
        with patch.object(paths_module, "MAX_PATH_UNITS", limit):
            self.assertEqual(
                self._fit(parent=parent, tail="t" * 200), "260503_ep123"
            )

    def test_too_deep_parent_keeps_identity_prefix(self):
        with patch.object(paths_module, "MAX_PATH_UNITS", 30):
            self.assertEqual(
                self._fit(parent="C:/very/deep/archive/root/26/05", tail="demo"),
                "260503_ep123",
            )

    def test_empty_tail_yields_the_identity_prefix_alone(self):
        with patch.object(paths_module, "MAX_PATH_UNITS", 259):
            self.assertEqual(
                self._fit(parent="C:/archive", tail=""), "260503_ep123"
            )

    def test_cjk_title_is_trimmed_within_the_component_limit(self):
        # Japanese titles measure 1 unit/char on Windows (UTF-16) but 3 on
        # POSIX (UTF-8); either way the component cap must hold.
        with patch.object(paths_module, "MAX_PATH_UNITS", 4096):
            fitted = self._fit(parent="C:/a", tail="お笑い芸人" * 40)

        self.assertLessEqual(measure(fitted), paths_module.MAX_COMPONENT_UNITS)


if __name__ == "__main__":
    unittest.main()
