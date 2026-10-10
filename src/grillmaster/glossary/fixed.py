"""Typed loader and prompt rendering for `fixed_glossary.json`.

The file has two sections: `talents`, comedy acts as units of an optional
group (組合) plus one or more members, and `others`, a flat list of program,
segment, brand and jargon terms. Each entry maps the original Japanese
source spellings (`jp`, only correct spellings; the model resolves ASR and
width drift against the whole table) to one Traditional Chinese rendering
(`zh`). The file is validated strictly: one malformed entry fails the load.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from grillmaster.core.json_artifact import read_model
from grillmaster.glossary.errors import GlossaryError

_DATA_DIR = Path(__file__).parent

FIXED_GLOSSARY_PATH = _DATA_DIR / "fixed_glossary.json"
# Curation rules for the glossary (structure, alias and rendering policy);
# the glossary-check agent reads it alongside the JSON.
FIXED_GLOSSARY_GUIDE_PATH = _DATA_DIR / "fixed_glossary.md"

_NonEmpty = Annotated[str, StringConstraints(min_length=1)]

_STRICT = ConfigDict(extra="forbid", frozen=True)


class GlossaryEntry(BaseModel):
    model_config = _STRICT

    jp: tuple[_NonEmpty, ...] = Field(min_length=1)
    zh: _NonEmpty


class TalentUnit(BaseModel):
    """One act: an optional group plus at least one member; `group` is
    `None` for a solo talent."""

    model_config = _STRICT

    group: GlossaryEntry | None = None
    members: tuple[GlossaryEntry, ...] = Field(min_length=1)

    def entries(self) -> list[GlossaryEntry]:
        """The group (if any), then every member: the prompt render order."""
        return [*([self.group] if self.group is not None else []), *self.members]


class FixedGlossary(BaseModel):
    model_config = _STRICT

    talents: tuple[TalentUnit, ...] = ()
    others: tuple[GlossaryEntry, ...] = ()

    def entries(self) -> list[GlossaryEntry]:
        """Every entry: talent units (in render order) before `others`."""
        return [
            *(entry for unit in self.talents for entry in unit.entries()),
            *self.others,
        ]


def load_fixed_glossary(path: Path = FIXED_GLOSSARY_PATH) -> FixedGlossary:
    """Read and validate the glossary; raises `GlossaryError` naming `path`."""
    try:
        return read_model(path, FixedGlossary)
    except OSError as error:
        raise GlossaryError(f"Cannot read fixed glossary {path}: {error}") from error
    except ValueError as error:  # pydantic's ValidationError, a bad encoding
        raise GlossaryError(f"Fixed glossary {path} is invalid:\n{error}") from error


def _format_entry(entry: GlossaryEntry) -> str:
    return f"{' / '.join(entry.jp)} → {entry.zh}"


_HEADER = (
    "\n【固定詞彙表（完整參照表；僅在該名稱實際出現時才套用，"
    "容許 ASR 誤聽，未出現者忽略）】\n"
)


def format_fixed_glossary_block(glossary: FixedGlossary) -> str:
    """The glossary as a prompt block: header, then grouped sections.

    Talents are listed under their group (or 單人) so an ambiguous member
    name carries its act as context; `others` stays flat. An empty section
    is left out.
    """
    sections: list[str] = []
    if glossary.talents:
        lines = ["〔藝人/組合〕"]
        for unit in glossary.talents:
            if unit.group is not None:
                lines.append(f"・組合：{_format_entry(unit.group)}")
            else:
                lines.append("・（單人）")
            lines.extend(f"    · {_format_entry(member)}" for member in unit.members)
        sections.append("\n".join(lines))
    if glossary.others:
        lines = ["〔節目/單元/品牌/術語〕"]
        lines.extend(f"- {_format_entry(entry)}" for entry in glossary.others)
        sections.append("\n".join(lines))
    return _HEADER + "\n".join(sections)
