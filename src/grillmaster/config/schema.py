"""JSON Schema for `grill.toml`, generated from `AppConfig`.

Committed as `grill.schema.json` at the repo root; a `#:schema
./grill.schema.json` first line in `grill.toml` lets Taplo / Even Better TOML
validate and complete the file. Regenerate after changing `config.model`:

    uv run poe schema
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from grillmaster.config.model import AppConfig
from grillmaster.core.fs import atomic_write_text


def render_config_json_schema() -> str:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **AppConfig.model_json_schema(),
    }
    return json.dumps(schema, ensure_ascii=False, indent=2) + "\n"


if __name__ == "__main__":
    match sys.argv[1:]:
        case [output]:
            atomic_write_text(Path(output), render_config_json_schema())
            print(f"Wrote {output}")  # noqa: T201 - CLI output
        case _:
            sys.exit("usage: python -m grillmaster.config.schema <output.json>")
