"""Hand-curated jp-aliases→zh fixed glossary, injected whole into prompts."""

import json
from dataclasses import dataclass
from pathlib import Path

from loguru import logger


FIXED_GLOSSARY_PATH = Path(__file__).parent / "fixed_glossary.json"

# An entry maps a list of JP source aliases to a single ZH target. The alias
# list holds only the original correct source spellings; the model resolves
# ASR/script/width drift against the whole table rather than it being
# enumerated here.
FixedGlossaryEntry = tuple[list[str], str]


@dataclass(frozen=True)
class TalentUnit:
    """One act: an optional 組合 plus one-or-more members.

    `group` is None for a solo talent. `members` is always non-empty — a unit
    whose members all fail validation is dropped at load time so a dangling
    組合 label never reaches the prompt.
    """

    group: FixedGlossaryEntry | None
    members: tuple[FixedGlossaryEntry, ...]

    def entries(self) -> list[FixedGlossaryEntry]:
        """Group (if any) first, then every member — the prompt render order."""
        out: list[FixedGlossaryEntry] = []
        if self.group is not None:
            out.append(self.group)
        out.extend(self.members)
        return out


@dataclass(frozen=True)
class FixedGlossary:
    """Parsed glossary: grouped talent units plus flat non-person entries."""

    talents: tuple[TalentUnit, ...] = ()
    others: tuple[FixedGlossaryEntry, ...] = ()

    def entries(self) -> list[FixedGlossaryEntry]:
        """Every entry, talent units (in render order) before `others`."""
        return [
            *(entry for unit in self.talents for entry in unit.entries()),
            *self.others,
        ]


def _parse_mapping_block(obj: object, ctx: str) -> FixedGlossaryEntry | None:
    """Validate one {jp:[...], zh:""} block.

    Bad shape → warn and return None so a typo never breaks the pipeline.
    """
    if not isinstance(obj, dict):
        logger.warning(f"[fixed-glossary] Skipping non-object {ctx}: {obj!r}")
        return None
    jp = obj.get("jp")
    zh = obj.get("zh")
    if (
        not isinstance(jp, list)
        or not jp
        or not all(isinstance(a, str) and a for a in jp)
    ):
        logger.warning(f"[fixed-glossary] Skipping {ctx} with bad 'jp' field: {obj!r}")
        return None
    if not isinstance(zh, str) or not zh:
        logger.warning(f"[fixed-glossary] Skipping {ctx} with bad 'zh' field: {obj!r}")
        return None
    return (list(jp), zh)


def _parse_talent_unit(obj: object, idx: int) -> TalentUnit | None:
    """Validate one talent unit.

    A bad/absent `group` degrades the unit to solo (kept if members valid);
    a unit with zero valid members is dropped entirely.
    """
    if not isinstance(obj, dict):
        logger.warning(f"[fixed-glossary] Skipping non-object talents[{idx}]: {obj!r}")
        return None
    group: FixedGlossaryEntry | None = None
    if obj.get("group") is not None:
        group = _parse_mapping_block(obj["group"], f"talents[{idx}].group")
    members_raw = obj.get("members")
    if not isinstance(members_raw, list) or not members_raw:
        logger.warning(
            f"[fixed-glossary] Skipping talents[{idx}] with bad 'members': {obj!r}"
        )
        return None
    members: list[FixedGlossaryEntry] = []
    for j, member in enumerate(members_raw):
        parsed = _parse_mapping_block(member, f"talents[{idx}].members[{j}]")
        if parsed is not None:
            members.append(parsed)
    if not members:
        logger.warning(
            f"[fixed-glossary] Skipping talents[{idx}] with no valid members: {obj!r}"
        )
        return None
    return TalentUnit(group, tuple(members))


def load_fixed_glossary(path: Path = FIXED_GLOSSARY_PATH) -> FixedGlossary:
    """Load and validate the fixed glossary file.

    The glossary is a required pipeline input: a missing or unparsable file,
    or a wrong top-level/section shape, raises. Individual malformed entries
    are skipped with a warning so one typo never breaks the whole pipeline.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(
            f"fixed glossary {path}: expected an object at top level, got "
            f"{type(raw).__name__}"
        )
    talents_raw = raw.get("talents", [])
    others_raw = raw.get("others", [])
    for section, value in (("talents", talents_raw), ("others", others_raw)):
        if not isinstance(value, list):
            raise ValueError(
                f"fixed glossary {path}: expected a list for '{section}', "
                f"got {type(value).__name__}"
            )

    talents = [
        unit
        for i, unit_raw in enumerate(talents_raw)
        if (unit := _parse_talent_unit(unit_raw, i)) is not None
    ]
    others = [
        entry
        for i, entry_raw in enumerate(others_raw)
        if (entry := _parse_mapping_block(entry_raw, f"others[{i}]")) is not None
    ]
    return FixedGlossary(tuple(talents), tuple(others))


def _format_entry(entry: FixedGlossaryEntry) -> str:
    aliases, zh = entry
    return f"{' / '.join(aliases)} → {zh}"


_HEADER = (
    "\n【固定詞彙表（完整參照表；僅在該名稱實際出現時才套用，"
    "容許 ASR 誤聽，未出現者忽略）】\n"
)


def format_fixed_glossary_block(glossary: FixedGlossary) -> str:
    """Render the glossary as the prompt block (header + grouped sections).

    Owns all glossary→prompt text shaping so pre_pass holds no formatting
    knowledge. Talents are grouped under their 組合 (or 單人) so an ambiguous
    member token carries disambiguation context; `others` stays a flat list.
    An empty section is omitted entirely.
    """
    sections: list[str] = []
    if glossary.talents:
        lines = ["〔藝人/組合〕"]
        for unit in glossary.talents:
            if unit.group is not None:
                lines.append(f"・組合：{_format_entry(unit.group)}")
            else:
                lines.append("・（單人）")
            for member in unit.members:
                lines.append(f"    · {_format_entry(member)}")
        sections.append("\n".join(lines))
    if glossary.others:
        lines = ["〔節目/單元/品牌/術語〕"]
        lines.extend(f"- {_format_entry(entry)}" for entry in glossary.others)
        sections.append("\n".join(lines))
    return _HEADER + "\n".join(sections)
