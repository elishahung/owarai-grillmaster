"""One-off conversion of the legacy `config.json` + `.env` into `grill.toml`.

    uv run python scripts/migrate_config.py [--root DIR]           # print TOML
    uv run python scripts/migrate_config.py [--root DIR] --apply   # write it

Reads the legacy program rules (`config.json`) and settings (`.env`) from
`--root` (default: the current directory) and renders a `grill.toml`
validated by `grillmaster.config.model.validate_config`. The TOML goes to
stdout (dry run) or to `<root>/grill.toml` (`--apply`, which refuses to
overwrite an existing file); notes go to stderr. `.env` is never rewritten:
only `ELEVENLABS_API_KEY` stays there, and the notes list the keys that are
no longer read. Secret values are never printed.

Delete this script once the real `grill.toml` exists.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

import tomlkit
from dotenv import dotenv_values
from pydantic import TypeAdapter

from grillmaster.config.model import validate_config
from grillmaster.core.model_spec import Backend, ModelSpec

CONFIG_JSON = "config.json"
ENV_FILE = ".env"
GRILL_TOML = "grill.toml"
SCHEMA_LINE = "#:schema ./grill.schema.json"

SECRET_KEYS = frozenset({"ELEVENLABS_API_KEY"})

# Legacy `.env` key -> (grill.toml section, key, value type).
SETTING_KEYS: dict[str, tuple[str, str, type]] = {
    "ARCHIVED_PATH": ("paths", "archive", str),
    "PACKAGE_PATH": ("paths", "package", str),
    "COOKIES_TXT_PATH": ("paths", "cookies", str),
    "AGENT_TIMEOUT_MINUTES": ("agents", "timeout_minutes", int),
    "AGENT_CONCURRENCY": ("agents", "max_concurrent", int),
    "ELEVENLABS_STT_MODEL": ("asr", "model", str),
    "ELEVENLABS_STT_LANGUAGE_CODE": ("asr", "language", str),
    "CHUNK_CHAR_LIMIT": ("translate", "chunk_char_limit", int),
    "CHUNK_MAX_RETRIES": ("translate", "chunk_attempts", int),
    "PREPASS_FRAME_INTERVAL_SECONDS": ("translate", "prepass_frame_interval_s", int),
    "CHUNK_FRAME_INTERVAL_SECONDS": ("translate", "chunk_frame_interval_s", int),
    "VIDEO_FRAME_MAX_SIDE": ("translate", "frame_max_side", int),
    "ENABLE_OFFICIAL_SUBTITLES": ("features", "official_subtitles", bool),
    "ENABLE_COVER_GENERATION": ("features", "cover", bool),
    "ENABLE_BROADCAST_DATE_AGENT_FALLBACK": ("features", "date_research", bool),
    "ENABLE_PACKAGE_TITLE_SUGGESTION": ("features", "title_suggestion", bool),
}
# Legacy model-spec key -> `[agents.roles]` key, with the legacy default.
ROLE_KEYS: dict[str, tuple[str, str | None]] = {
    "AGENT_PREPASS_MODEL": ("prepass", "agy/gemini-3.1-pro"),
    "AGENT_CHUNK_MODEL": ("chunk", "agy/gemini-3.1-pro"),
    "AGENT_POSTPROCESS_MODEL": ("postprocess", "codex/gpt-5.6-sol/medium"),
    "AGENT_COMMON_MODEL": ("utility", "codex/gpt-5.5/medium"),
    "AGENT_CHAT_MODEL": ("chat", None),
}
SECTION_ORDER = ("paths", "agents", "asr", "translate", "features", "package")
# Legacy instruction step -> new stage key.
INSTRUCTION_KEYS = {
    "common": "common",
    "pre_pass": "prepass",
    "translate": "chunks",
    "refine": "refine",
    "glossary_check": "glossary",
}
# Legacy packaging folders under PACKAGE_PATH; the new code reads pools/<name>.
LEGACY_PLACEHOLDER_DIR = "placeholder"
LEGACY_NOISE_DIR = "noise"


def _convert(key: str, value: str, kind: type) -> object:
    if kind is str:
        return value
    try:
        return TypeAdapter(kind).validate_python(value)
    except ValueError as error:
        raise ValueError(f"{key}: cannot read {value!r} as {kind.__name__}") from error


def _image_role(utility: ModelSpec, postprocess: ModelSpec, notes: list[str]) -> str:
    """Cover generation was hard-wired to Codex at the utility effort."""
    for spec in (utility, postprocess):
        if spec.backend is Backend.CODEX:
            return f"codex/{spec.model}/{utility.effort}"
    notes.append(
        "agents.roles.image: no codex model configured; set a codex model by hand"
    )
    return f"codex/CHANGE-ME/{utility.effort}"


def settings_tables(env: dict[str, str], notes: list[str]) -> dict[str, dict[str, Any]]:
    """`grill.toml` sections from the legacy settings (unset keys keep defaults)."""
    tables: dict[str, dict[str, Any]] = {}
    for key, (section, name, kind) in SETTING_KEYS.items():
        if (value := env.get(key)) is not None:
            tables.setdefault(section, {})[name] = _convert(key, value, kind)
    roles: dict[str, str] = {}
    for key, (role, default) in ROLE_KEYS.items():
        text = env.get(key) or default
        if text is not None:
            roles[role] = str(ModelSpec.parse(text))
            if key not in env:
                notes.append(f"agents.roles.{role}: {key} unset, legacy default used")
    roles["image"] = _image_role(
        ModelSpec.parse(roles["utility"]), ModelSpec.parse(roles["postprocess"]), notes
    )
    tables.setdefault("agents", {})["roles"] = roles
    return tables


def package_table(package_root: Path | None, notes: list[str]) -> dict[str, Any] | None:
    """The legacy placeholder clip becomes a remix-only `judge` insert.

    Legacy packaging skipped the clip when `<package>/placeholder` was absent;
    a configured insert pool is mandatory, so it is only declared when the
    folder exists. The folders themselves are not moved here.
    """
    if package_root is None:
        return None
    pools = package_root / "pools"
    placeholder = package_root / LEGACY_PLACEHOLDER_DIR
    noise_root = package_root / LEGACY_NOISE_DIR
    noise_sets = (
        sorted(p.name for p in noise_root.iterdir() if p.is_dir())
        if noise_root.is_dir()
        else []
    )
    notes.extend(
        f"move noise set {noise_root / name} -> {pools / name}" for name in noise_sets
    )
    if not placeholder.is_dir():
        return None
    notes.append(f"move {placeholder} -> {pools / LEGACY_PLACEHOLDER_DIR}")
    return {
        "inserts": [
            {"pool": LEGACY_PLACEHOLDER_DIR, "output": "judge", "when": "remix"}
        ]
    }


def program_tables(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """`[programs.series.*]` / `[programs.channel.*]` from `config.json`."""
    programs: dict[str, dict[str, Any]] = {}
    for section in ("series", "channel"):
        entries = document.get(section) or {}
        converted: dict[str, Any] = {}
        for name, entry in entries.items():
            rule: dict[str, Any] = {}
            if entry.get("remix") is not None:
                rule["remix"] = bool(entry["remix"])
            instruction = {
                INSTRUCTION_KEYS[step]: text
                for step, text in (entry.get("instruction") or {}).items()
                if text is not None
            }
            if instruction:
                rule["instruction"] = instruction
            unknown = set(entry) - {"remix", "instruction"}
            if unknown:
                raise ValueError(f"{section} '{name}': unknown keys {sorted(unknown)}")
            converted[name] = rule
        if converted:
            programs[section] = converted
    return programs


def _toml_value(value: object) -> object:
    if isinstance(value, str):
        # A multi-line string would turn `\r\n` into `\n`; the escaped
        # single-line form keeps it.
        if "\n" in value and "\r" not in value:
            return tomlkit.string(value, multiline=True)
        if "\\" in value and "'" not in value:
            return tomlkit.string(value, literal=True)
    return value


def _toml_table(data: dict[str, Any]) -> Any:
    """A TOML table; one holding only sub-tables gets no header of its own."""
    nested = all(isinstance(value, dict | list) for value in data.values())
    table = tomlkit.table(is_super_table=bool(data) and nested)
    for key, value in data.items():
        if isinstance(value, dict):
            table[key] = _toml_table(value)
        elif isinstance(value, list):
            array = tomlkit.aot()
            for item in value:
                array.append(_toml_table(item))
            table[key] = array
        else:
            table[key] = _toml_value(value)
    return table


def render_toml(
    tables: dict[str, dict[str, Any]], programs: dict[str, dict[str, Any]]
) -> str:
    doc = tomlkit.document()
    for section in SECTION_ORDER:
        if section in tables:
            doc[section] = _toml_table(tables[section])
    if programs:
        doc["programs"] = _toml_table(programs)
    return f"{SCHEMA_LINE}\n{tomlkit.dumps(doc)}"


def build(root: Path) -> tuple[str, list[str]]:
    """Render and validate the TOML; returns it with the migration notes."""
    notes: list[str] = []
    env_path = root / ENV_FILE
    raw_env = dotenv_values(env_path) if env_path.exists() else {}
    env = {key.upper(): value for key, value in raw_env.items() if value is not None}
    known = SECRET_KEYS | set(SETTING_KEYS) | set(ROLE_KEYS)
    for key in sorted(env):
        if key in SECRET_KEYS:
            notes.append(f".env {key}: stays in .env")
        elif key in known:
            notes.append(f".env {key}: now in grill.toml, no longer read from .env")
        else:
            notes.append(f".env {key}: unknown legacy key, ignored")

    tables = settings_tables(env, notes)
    package_path = tables.get("paths", {}).get("package")
    if package := package_table(root / package_path if package_path else None, notes):
        tables["package"] = package

    config_path = root / CONFIG_JSON
    document: dict[str, Any] = (
        json.loads(config_path.read_text(encoding="utf-8-sig"))
        if config_path.exists()
        else {}
    )
    programs = program_tables(document)
    text = render_toml(tables, programs)
    parsed = tomllib.loads(text)
    # The instructions are long multi-line prose; a quoting slip in the
    # rendering must fail here instead of altering them silently.
    if parsed.get("programs", {}) != programs:
        raise ValueError("rendered grill.toml does not round-trip the program rules")
    validate_config(parsed, root=root)
    notes.append(
        f"programs: {len(programs.get('series', {}))} series,"
        f" {len(programs.get('channel', {}))} channels; grill.toml validates"
    )
    return text, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert config.json + .env to grill.toml"
    )
    parser.add_argument("--root", type=Path, default=Path(), help="legacy working root")
    parser.add_argument("--apply", action="store_true", help=f"write {GRILL_TOML}")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    root: Path = args.root.absolute()
    target = root / GRILL_TOML
    if args.apply and target.exists():
        parser.error(f"{target} already exists; not overwriting it")
    text, notes = build(root)
    for note in notes:
        print(f"note: {note}", file=sys.stderr)
    if args.apply:
        target.write_text(text, encoding="utf-8")
        print(f"wrote {target}", file=sys.stderr)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
