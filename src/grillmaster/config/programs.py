"""Per-program rules: merge the matching `[programs.*]` entries, register new ones.

A program matches up to two entries, its channel and its series. They merge
broadest first (channel, then series): `remix` is on when either entry sets
it, and each stage's instruction text is every entry's `common` text followed
by its stage text, in that order. Inserts are the ones no program lists plus
the ones a matching entry lists, in declaration order.

The download stage registers names it has not seen as empty entries, ready
for a hand edit; nothing else ever writes `grill.toml`.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING

import tomlkit
from loguru import logger

from grillmaster.config.errors import ConfigError
from grillmaster.core.fs import atomic_write_text
from grillmaster.core.prompts import join_sections

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.config.model import (
        InsertRule,
        PackageConfig,
        ProgramInstruction,
        ProgramsConfig,
    )
    from grillmaster.core.stage_key import StageKey

_BOM = "﻿"


@dataclass(frozen=True, slots=True)
class ProgramRules:
    """The merged rules of every entry matching one program."""

    remix: bool = False
    inserts: tuple[InsertRule, ...] = ()
    instructions: tuple[ProgramInstruction, ...] = ()

    def instruction_text(self, stage: StageKey) -> str:
        """Configured text for `stage` (per entry, `common` then the stage's own).

        Empty when nothing is configured; prompts wrap it with
        `core.prompts.render_program_instruction`.
        """
        return join_sections(
            *(
                text
                for instruction in self.instructions
                for text in (instruction.common, instruction.stage_text(stage))
            )
        )

    def inserts_for(self, *, remix: bool) -> list[InsertRule]:
        """Inserts for one deliverable: `when = "remix"` ones only for a remix."""
        return [insert for insert in self.inserts if insert.when == "always" or remix]


def resolve_program_rules(
    programs: ProgramsConfig,
    package: PackageConfig,
    *,
    series: str | None,
    channel: str | None,
) -> ProgramRules:
    """Merge the channel then series entries for a program."""
    entries = [
        rule
        for rule in (
            programs.channel.get(channel) if channel else None,
            programs.series.get(series) if series else None,
        )
        if rule is not None
    ]
    scoped = {name for _, _, rule in programs.rules() for name in rule.inserts}
    listed = {name for rule in entries for name in rule.inserts}
    return ProgramRules(
        remix=any(rule.remix for rule in entries),
        inserts=tuple(
            insert
            for insert in package.inserts
            if insert.output not in scoped or insert.output in listed
        ),
        instructions=tuple(rule.instruction for rule in entries),
    )


def register_program(
    toml_path: Path, *, series: str | None, channel: str | None
) -> list[str]:
    """Append empty `[programs.series."X"]` / `[programs.channel."Y"]` entries.

    Only names not listed yet are added, at the end of the file, so the
    user's comments and layout stay byte-for-byte. Returns the labels of the
    entries added; the file is not touched when there are none.
    """
    # newline="": keep the file's own line endings for the byte-exact rewrite.
    raw = toml_path.read_text(encoding="utf-8", newline="")
    bom = _BOM if raw.startswith(_BOM) else ""
    text = raw.removeprefix(bom)
    programs = tomllib.loads(text).get("programs", {})

    missing: dict[str, dict[str, dict[str, object]]] = {}
    registered: list[str] = []
    for section, name in (("series", series), ("channel", channel)):
        if name and name not in programs.get(section, {}):
            missing.setdefault(section, {})[name] = {}
            registered.append(f"{section} '{name}'")
    if not registered:
        return []

    newline = "\r\n" if "\r\n" in text else "\n"
    # tomlkit renders the headers so names needing quotes or escapes are right.
    snippet = tomlkit.dumps({"programs": missing}).strip().replace("\n", newline)
    if text and not text.endswith("\n"):
        text += newline
    updated = f"{text}{newline if text else ''}{snippet}{newline}"
    try:
        tomllib.loads(updated)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(
            f"Cannot register {', '.join(registered)} in {toml_path}: "
            f"appending the entries would break the file ({error})"
        ) from error
    atomic_write_text(toml_path, bom + updated)
    logger.info(f"Registered in {toml_path}: {', '.join(registered)}")
    return registered
