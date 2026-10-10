"""JSON Schema for `config.json`, generated from the program-entry model.

The schema is committed as `config.schema.json` at the repo root, and
`config.json` points at it with `"$schema": "./config.schema.json"` so the IDE
validates and completes the file. After changing `ProgramEntry`, regenerate it:

    uv run python -m services.program_config.schema
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .config import SCHEMA_FILE_NAME, ProgramEntry

REPO_SCHEMA_PATH = Path(__file__).resolve().parents[4] / SCHEMA_FILE_NAME


# Schema-only view: loading never goes through this model, since entries are
# validated one by one so a broken entry is skipped instead of disabling the
# file. The docstring is the description the IDE shows.
class _ConfigDocument(BaseModel):
    """Owarai GrillMaster per-program rules, keyed by series and channel name."""

    # Other top-level keys belong to whatever else reads the file.
    model_config = ConfigDict(title="Owarai GrillMaster config.json", extra="allow")

    schema_ref: str | None = Field(
        default=None,
        alias="$schema",
        description="Path to this schema, for the IDE.",
    )
    series: dict[str, ProgramEntry] = Field(
        default_factory=dict,
        description="Rules keyed by the source program (series) name.",
    )
    channel: dict[str, ProgramEntry] = Field(
        default_factory=dict,
        description="Rules keyed by the broadcast station or uploader channel.",
    )


def config_json_schema() -> dict[str, Any]:
    """The JSON Schema document for `config.json`."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **_ConfigDocument.model_json_schema(by_alias=True),
    }


def write_config_json_schema(path: Path = REPO_SCHEMA_PATH) -> None:
    path.write_text(
        json.dumps(config_json_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    write_config_json_schema()
    print(f"Wrote {REPO_SCHEMA_PATH}")
