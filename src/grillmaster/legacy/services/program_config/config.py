"""Per-program rules keyed by the source series and channel.

`config.json` lives at the working-directory root (next to `projects/`) and is
git-ignored; `config.example.json` in the repo shows its shape. This module
owns its `series` and `channel` sections; other top-level keys are left alone
for whatever else comes to read the file. The download stage only ever appends
an empty entry for a program it has just seen; opting a series or channel into
remix packaging or giving it extra model instructions is a manual edit of that
entry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError, WithJsonSchema

CONFIG_FILE_NAME = "config.json"
SCHEMA_FILE_NAME = "config.schema.json"
_LEGACY_FILE_NAME = ".packagerc"

_SECTIONS = ("channel", "series")  # broadest first: merge order

_INSTRUCTION_TEMPLATE = (
    Path(__file__).parent / "prompts" / "program_instruction.md"
).read_text(encoding="utf-8")

InstructionStep = Literal["pre_pass", "translate", "refine", "glossary_check"]
# `common` is added to every step, ahead of that step's own text.
InstructionKey = Literal["common"] | InstructionStep

# Shown by the IDE for each `instruction` key (see `config.schema.json`).
INSTRUCTION_KEY_DOCS: dict[InstructionKey, str] = {
    "common": "Added to every step below, ahead of that step's own text.",
    "pre_pass": (
        "Whole-film analysis: cast, proper nouns, glossary, catchphrases, "
        "segment summaries."
    ),
    "translate": "Concurrent chunk translation into Traditional Chinese.",
    "refine": "Agent polish of the translated subtitles.",
    "glossary_check": ("Agent terminology/fact check against the fixed glossary."),
}

# pydantic drops the Literal key constraint from a dict's JSON schema, so the
# instruction object is spelled out for IDE validation and completion.
_INSTRUCTION_JSON_SCHEMA = {
    "anyOf": [
        {
            "type": "object",
            "description": "Extra model instructions for this program.",
            "properties": {
                key: {"type": ["string", "null"], "description": doc}
                for key, doc in INSTRUCTION_KEY_DOCS.items()
            },
            "additionalProperties": False,
        },
        {"type": "null"},
    ]
}


class ProgramEntry(BaseModel):
    """Rules for one series or channel name."""

    # Reject unknown keys so a misspelled rule or step fails loudly instead of
    # being silently dropped.
    model_config = ConfigDict(extra="forbid")

    remix: bool | None = Field(
        default=None,
        description=(
            "Force remix packaging with the default noise set when --remix "
            "is not passed."
        ),
    )
    instruction: Annotated[
        dict[InstructionKey, str | None] | None,
        WithJsonSchema(_INSTRUCTION_JSON_SCHEMA),
    ] = None


@dataclass(frozen=True)
class ProgramRules:
    """The merged rules of every entry matching one program."""

    remix: bool = False
    entries: tuple[ProgramEntry, ...] = field(default_factory=tuple)

    def instructions(self, step: InstructionStep) -> list[str]:
        """Non-blank texts for `step`: per entry, `common` then the step."""
        texts = [
            (entry.instruction or {}).get(key)
            for entry in self.entries
            for key in ("common", step)
        ]
        return [text.strip() for text in texts if text and text.strip()]

    def render_instruction(self, step: InstructionStep) -> str | None:
        """The prompt section for `step`, or None when nothing is configured."""
        texts = self.instructions(step)
        if not texts:
            return None
        logger.info(f"Program instructions for {step}: {len(texts)} block(s)")
        return _INSTRUCTION_TEMPLATE.replace("{instructions}", "\n\n".join(texts))


def config_path() -> Path:
    """Path to `config.json`, resolved against the working directory."""
    return Path(CONFIG_FILE_NAME)


def load_program_rules(*, series: str | None, channel: str | None) -> ProgramRules:
    """Merge the channel then series entries for a program.

    Each entry is validated on its own: a broken entry is skipped with a
    warning, and an unreadable file degrades to no rules at all.
    """
    names = {"channel": channel, "series": series}
    if not any(names.values()):
        return ProgramRules()
    path = config_path()
    try:
        document = _read_document(path)
    except ValueError as e:
        logger.warning(f"Ignoring {path}: {e}")
        return ProgramRules()

    entries: list[ProgramEntry] = []
    for section in _SECTIONS:
        name = names[section]
        raw = _section(document, section).get(name) if name else None
        if raw is None:
            continue
        try:
            entries.append(ProgramEntry.model_validate(raw))
        except ValidationError as e:
            logger.warning(f"Ignoring {path} {section} '{name}': {e}")
    return ProgramRules(
        remix=any(entry.remix for entry in entries), entries=tuple(entries)
    )


def register_program(*, series: str | None, channel: str | None) -> None:
    """Add empty `config.json` entries for names that are not listed yet.

    Works on the raw document so hand-written content — other top-level keys,
    entries this version cannot parse — is written back untouched.
    """
    path = config_path()
    try:
        document = _read_document(path)
    except ValueError as e:
        # Never clobber a file the maintainer is editing by hand.
        logger.warning(f"Not updating {path}: {e}")
        return
    if not document:
        # A file this creates points the IDE at the repo's schema.
        document["$schema"] = f"./{SCHEMA_FILE_NAME}"

    registered: list[str] = []
    for section, name in (("series", series), ("channel", channel)):
        if not name:
            continue
        entries = document.setdefault(section, {})
        if not isinstance(entries, dict):
            logger.warning(f"Not updating {path}: '{section}' is not an object")
            return
        if name not in entries:
            entries[name] = {}
            registered.append(f"{section} '{name}'")
    if not registered:
        return

    try:
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning(f"Failed to write {path}: {e}")
        return
    logger.info(f"Registered in {path}: {', '.join(registered)}")


def _read_document(path: Path) -> dict[str, Any]:
    """Parse `config.json` as a JSON object; an absent file is empty."""
    if not path.exists():
        legacy = path.with_name(_LEGACY_FILE_NAME)
        if legacy.exists():
            logger.warning(f"{legacy} is no longer read; rename it to {path.name}")
        return {}
    try:
        # utf-8-sig: the file is hand-edited, and Windows editors add a BOM.
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as e:
        raise ValueError(f"unreadable config: {e}") from e
    if not isinstance(document, dict):
        raise ValueError("top level is not a JSON object")
    return document


def _section(document: dict[str, Any], section: str) -> dict[str, Any]:
    entries = document.get(section)
    return entries if isinstance(entries, dict) else {}
