from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from grillmaster.config.model import AppConfig, InsertRule, ProgramRule, validate_config
from grillmaster.config.schema import render_config_json_schema

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "grill.schema.json"
EXAMPLE_PATH = REPO_ROOT / "grill.example.toml"


def test_committed_schema_is_current():
    # Regenerate with `uv run poe schema` after changing `config.model`.
    assert SCHEMA_PATH.read_text(encoding="utf-8") == render_config_json_schema()


def test_example_points_at_the_schema():
    first_line = EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "#:schema ./grill.schema.json"


def test_example_is_a_valid_config():
    data = tomllib.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    config = validate_config(data, root=Path("/grill"))
    assert config.programs.series["水曜日のダウンタウン"].inserts == ["judge"]


def test_example_spells_out_every_setting():
    data = tomllib.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    series = data["programs"]["series"]["水曜日のダウンタウン"]
    missing = [
        *_missing_keys(AppConfig, data),
        *_missing_keys(InsertRule, data["package"]["inserts"][0], "package.inserts."),
        *_missing_keys(ProgramRule, series, "programs.series.*."),
    ]
    assert missing == []


def _missing_keys(
    model: type[BaseModel], data: dict[str, Any], prefix: str = ""
) -> list[str]:
    """Fields of `model` (recursively through sub-models) absent from `data`."""
    missing: list[str] = []
    for name, field in model.model_fields.items():
        if name not in data:
            missing.append(prefix + name)
            continue
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            missing += _missing_keys(annotation, data[name], f"{prefix}{name}.")
    return missing
