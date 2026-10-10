import json
import tempfile
import unittest
from pathlib import Path

from services.fixed_glossary import (
    FixedGlossary,
    TalentUnit,
    format_fixed_glossary_block,
    load_fixed_glossary,
)


def U(group, members):
    """Build a TalentUnit (members given as a plain list of entries)."""
    return TalentUnit(group, tuple(members))


def G(*talents, others=()):
    """Build a FixedGlossary from talent units and other entries."""
    return FixedGlossary(tuple(talents), tuple(others))


class FixedGlossaryEntriesTests(unittest.TestCase):
    def test_talents_in_render_order_then_others(self):
        glossary = G(
            U((["かまいたち"], "鎌鼬"), [(["山内"], "山內"), (["濱家"], "濱家")]),
            U(None, [(["ヒコロヒー"], "Hikorohee")]),
            others=((["ボケ"], "裝傻"),),
        )
        self.assertEqual(
            [zh for _, zh in glossary.entries()],
            ["鎌鼬", "山內", "濱家", "Hikorohee", "裝傻"],
        )

    def test_empty_glossary_has_no_entries(self):
        self.assertEqual(FixedGlossary().entries(), [])


class LoadFixedGlossaryTests(unittest.TestCase):
    def _load(self, obj):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "g.json"
            p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
            return load_fixed_glossary(path=p)

    def test_bad_top_level_type_raises(self):
        with self.assertRaises(ValueError):
            self._load([{"jp": ["x"], "zh": "y"}])

    def test_bad_section_type_raises(self):
        with self.assertRaises(ValueError):
            self._load({"talents": {}, "others": []})

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_fixed_glossary(path=Path("does/not/exist.json"))

    def test_talents_and_others_both_loaded(self):
        g = self._load(
            {
                "talents": [
                    {
                        "group": {"jp": ["かまいたち"], "zh": "鎌鼬"},
                        "members": [{"jp": ["山内"], "zh": "山內"}],
                    },
                    {"members": [{"jp": ["ヒコロヒー"], "zh": "Hikorohee"}]},
                ],
                "others": [{"jp": ["ボケ"], "zh": "裝傻"}],
            }
        )
        self.assertEqual(len(g.talents), 2)
        self.assertEqual(g.talents[0].group, (["かまいたち"], "鎌鼬"))
        self.assertIsNone(g.talents[1].group)
        self.assertEqual({zh for _, zh in g.others}, {"裝傻"})

    def test_malformed_unit_skipped(self):
        g = self._load(
            {
                "talents": [
                    "not-an-object",
                    {"members": []},  # empty members → dropped
                    {  # bad optional group → dropped group, unit kept
                        "group": {"jp": [], "zh": "x"},
                        "members": [{"jp": ["盛山"], "zh": "盛山"}],
                    },
                    {"members": [{"jp": ["valid"], "zh": "OK"}]},
                ],
                "others": [
                    {"jp": "notalist", "zh": "bad"},
                    {"jp": ["good"], "zh": "Good"},
                ],
            }
        )
        self.assertEqual(len(g.talents), 2)
        kept_group = g.talents[0]
        self.assertIsNone(kept_group.group)
        self.assertEqual([zh for _, zh in kept_group.members], ["盛山"])
        self.assertEqual({zh for _, zh in g.others}, {"Good"})

    def test_loads_real_file_shape(self):
        # Bundled file must parse non-empty into the new grouped structure.
        glossary = load_fixed_glossary()
        self.assertTrue(glossary.entries())
        for unit in glossary.talents:
            self.assertIsInstance(unit, TalentUnit)
            self.assertTrue(unit.members)
        for aliases, zh in glossary.entries():
            self.assertIsInstance(aliases, list)
            self.assertTrue(all(isinstance(a, str) and a for a in aliases))
            self.assertIsInstance(zh, str)
            self.assertTrue(zh)


class FormatFixedGlossaryBlockTests(unittest.TestCase):
    def test_grouped_layout_with_group_and_solo(self):
        glossary = G(
            U((["かまいたち"], "鎌鼬"), [(["山内", "山內健司"], "山內")]),
            U(None, [(["ヒコロヒー"], "Hikorohee")]),
            others=((["ボケ"], "裝傻"),),
        )
        out = format_fixed_glossary_block(glossary)
        self.assertIn("〔藝人/組合〕", out)
        self.assertIn("・組合：かまいたち → 鎌鼬", out)
        self.assertIn("    · 山内 / 山內健司 → 山內", out)
        self.assertIn("・（單人）", out)
        self.assertIn("    · ヒコロヒー → Hikorohee", out)
        self.assertIn("〔節目/單元/品牌/術語〕", out)
        self.assertIn("- ボケ → 裝傻", out)

    def test_header_marks_a_reference_table(self):
        out = format_fixed_glossary_block(G(others=((["ボケ"], "裝傻"),)))
        self.assertIn("完整參照表", out)

    def test_empty_section_omitted(self):
        only_others = format_fixed_glossary_block(G(others=((["ボケ"], "裝傻"),)))
        self.assertNotIn("〔藝人/組合〕", only_others)
        self.assertIn("〔節目/單元/品牌/術語〕", only_others)

        only_talents = format_fixed_glossary_block(G(U(None, [(["ヤス"], "Yasu")])))
        self.assertIn("〔藝人/組合〕", only_talents)
        self.assertNotIn("〔節目/單元/品牌/術語〕", only_talents)


if __name__ == "__main__":
    unittest.main()
