"""A person or group the source platform credits for the program: fetched
by `sources`, recorded in the project state, quoted in agent prompts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.core.models import FrozenModel

if TYPE_CHECKING:
    from collections.abc import Iterable


class Talent(FrozenModel):
    id: str
    name: str
    name_kana: str | None = None
    roles: tuple[str, ...] = ()


def render_talent_lines(talents: Iterable[Talent]) -> list[str]:
    """One `- name / kana (role, role)` prompt line per talent."""
    lines: list[str] = []
    for talent in talents:
        kana = f" / {talent.name_kana}" if talent.name_kana else ""
        roles = f" ({', '.join(talent.roles)})" if talent.roles else ""
        lines.append(f"- {talent.name}{kana}{roles}")
    return lines
