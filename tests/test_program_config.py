import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import get_args
from unittest.mock import patch

from services.program_config import (
    config as program_config,
    load_program_rules,
    register_program,
)
from services.program_config.schema import (
    REPO_SCHEMA_PATH,
    config_json_schema,
)


class ProgramConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="program-config-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        self.config_file = root / "config.json"
        patcher = patch.object(
            program_config, "config_path", return_value=self.config_file
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write_config(self, payload: dict) -> None:
        self.config_file.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def _read_config(self) -> dict:
        return json.loads(self.config_file.read_text(encoding="utf-8"))

    # -- registration -----------------------------------------------------

    def test_registers_new_names_with_empty_rules(self):
        register_program(series="ドキュメンタル", channel="Prime Video")

        self.assertEqual(
            self._read_config(),
            {
                "$schema": "./config.schema.json",
                "series": {"ドキュメンタル": {}},
                "channel": {"Prime Video": {}},
            },
        )
        self.assertEqual(next(iter(self._read_config())), "$schema")

    def test_registering_keeps_hand_written_content(self):
        existing = {
            "series": {
                "ドキュメンタル": {
                    "remix": True,
                    "instruction": {"common": "keep names"},
                },
                # Unparsable by this version, but still the maintainer's.
                "broken": {"remix": "sometimes"},
            },
            "other_tool": {"enabled": True},
        }
        self._write_config(existing)

        register_program(series="ドキュメンタル", channel="Prime Video")

        self.assertEqual(
            self._read_config(),
            {**existing, "channel": {"Prime Video": {}}},
        )

    def test_registering_nothing_new_leaves_the_file_alone(self):
        register_program(series=None, channel=None)

        self.assertFalse(self.config_file.exists())

    def test_unreadable_file_is_never_overwritten(self):
        self.config_file.write_text("{not json", encoding="utf-8")

        register_program(series="ドキュメンタル", channel=None)

        self.assertEqual(self.config_file.read_text(encoding="utf-8"), "{not json")
        self.assertEqual(
            load_program_rules(series="ドキュメンタル", channel=None),
            program_config.ProgramRules(),
        )

    def test_file_with_bom_is_read_and_updated(self):
        self.config_file.write_text(
            json.dumps({"series": {"show": {"remix": True}}}),
            encoding="utf-8-sig",
        )

        self.assertTrue(load_program_rules(series="show", channel=None).remix)
        register_program(series=None, channel="station")
        # An existing file is never given a `$schema` it did not have.
        self.assertEqual(
            json.loads(self.config_file.read_text(encoding="utf-8-sig")),
            {"series": {"show": {"remix": True}}, "channel": {"station": {}}},
        )

    def test_leftover_packagerc_is_reported_not_read(self):
        legacy = self.config_file.with_name(".packagerc")
        legacy.write_text(
            json.dumps({"series": {"show": {"remix": True}}}),
            encoding="utf-8",
        )

        with patch.object(program_config.logger, "warning") as warning:
            rules = load_program_rules(series="show", channel=None)

        self.assertFalse(rules.remix)
        self.assertIn(".packagerc", warning.call_args.args[0])

    # -- remix ------------------------------------------------------------

    def test_series_or_channel_rule_forces_remix(self):
        self._write_config(
            {
                "series": {"show": {"remix": True}},
                "channel": {"station": {"remix": True}},
            }
        )

        self.assertTrue(load_program_rules(series="show", channel=None).remix)
        self.assertTrue(load_program_rules(series=None, channel="station").remix)

    def test_listed_without_remix_or_unlisted_does_not_force_remix(self):
        self._write_config(
            {"series": {"show": {}}, "channel": {"station": {"remix": False}}}
        )

        self.assertFalse(load_program_rules(series="show", channel="station").remix)
        self.assertFalse(load_program_rules(series="other", channel="elsewhere").remix)

    def test_broken_entry_does_not_disable_other_entries(self):
        self._write_config(
            {
                "series": {"show": {"instruction": {"prepass": "typo"}}},
                "channel": {"station": {"remix": True}},
            }
        )

        rules = load_program_rules(series="show", channel="station")

        self.assertTrue(rules.remix)
        self.assertEqual(rules.instructions("pre_pass"), [])

    # -- instructions -----------------------------------------------------

    def test_instruction_combines_common_and_step_for_channel_then_series(
        self,
    ):
        self._write_config(
            {
                "series": {
                    "show": {
                        "instruction": {
                            "common": "series common",
                            "refine": "series refine",
                            "translate": "series translate",
                        }
                    }
                },
                "channel": {
                    "station": {
                        "instruction": {
                            "common": "channel common",
                            "refine": "channel refine",
                        }
                    }
                },
            }
        )

        rules = load_program_rules(series="show", channel="station")

        self.assertEqual(
            rules.instructions("refine"),
            [
                "channel common",
                "channel refine",
                "series common",
                "series refine",
            ],
        )
        section = rules.render_instruction("refine")
        assert section is not None
        self.assertIn("PROGRAM-SPECIFIC INSTRUCTIONS", section)
        self.assertIn("channel common\n\nchannel refine", section)
        self.assertNotIn("series translate", section)
        self.assertNotIn("{instructions}", section)

    def test_null_instruction_value_keeps_the_entry(self):
        self._write_config(
            {
                "series": {
                    "show": {
                        "remix": True,
                        "instruction": {"common": "always", "refine": None},
                    }
                }
            }
        )

        rules = load_program_rules(series="show", channel=None)

        self.assertTrue(rules.remix)
        self.assertEqual(rules.instructions("refine"), ["always"])

    def test_common_alone_applies_to_every_step(self):
        self._write_config({"series": {"show": {"instruction": {"common": "always"}}}})
        rules = load_program_rules(series="show", channel=None)

        for step in ("pre_pass", "translate", "refine", "glossary_check"):
            self.assertIn("always", rules.render_instruction(step) or "")

    def test_no_instruction_renders_nothing(self):
        self._write_config(
            {
                "series": {"show": {"remix": True}},
                "channel": {"station": {"instruction": {"common": "  "}}},
            }
        )

        self.assertIsNone(
            load_program_rules(series="show", channel="station").render_instruction(
                "pre_pass"
            )
        )
        self.assertIsNone(
            load_program_rules(series=None, channel=None).render_instruction("pre_pass")
        )


def _literal_values(tp: object) -> set[str]:
    args = get_args(tp)
    if not args:
        return {tp}
    return {value for arg in args for value in _literal_values(arg)}


class ConfigSchemaTests(unittest.TestCase):
    def test_instruction_schema_lists_exactly_the_model_keys(self):
        entry = config_json_schema()["$defs"]["ProgramEntry"]
        instruction = entry["properties"]["instruction"]["anyOf"][0]

        self.assertEqual(
            set(instruction["properties"]),
            _literal_values(program_config.InstructionKey),
        )
        self.assertFalse(instruction["additionalProperties"])
        self.assertFalse(entry["additionalProperties"])

    def test_root_schema_maps_names_to_program_entries(self):
        schema = config_json_schema()

        for section in ("series", "channel"):
            self.assertEqual(
                schema["properties"][section]["additionalProperties"],
                {"$ref": "#/$defs/ProgramEntry"},
            )
        self.assertIn("$schema", schema["properties"])

    def test_example_config_matches_the_schema_model(self):
        # Validated through the model the schema is generated from, so no
        # JSON Schema library is needed.
        example_path = REPO_SCHEMA_PATH.with_name("config.example.json")
        example = json.loads(example_path.read_text(encoding="utf-8"))

        self.assertEqual(example["$schema"], f"./{REPO_SCHEMA_PATH.name}")
        self.assertLessEqual(set(example), set(config_json_schema()["properties"]))
        for section in ("series", "channel"):
            for entry in example[section].values():
                program_config.ProgramEntry.model_validate(entry)


if __name__ == "__main__":
    unittest.main()
