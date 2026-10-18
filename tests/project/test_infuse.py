from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from typing import TYPE_CHECKING

from grillmaster.project.infuse import infuse_xml
from grillmaster.project.state import DateResearchRecord, now

if TYPE_CHECKING:
    from grillmaster.project.state import ProjectState


def fields(state: ProjectState) -> dict[str, str | None]:
    media = ET.fromstring(infuse_xml(state))  # noqa: S314 - our own output
    assert media.tag == "media"
    assert media.get("type") == "Other"
    return {child.tag: child.text for child in media}


def test_the_sidecar_carries_the_platform_title_description_and_date(
    state: ProjectState,
):
    state.name = "全力脱力タイムズ_ネプ"
    state.source.title = "全力！脱力タイムズ <ネプ> & 福本莉子"
    state.source.description = "▼一行目\n▼二行目"
    state.broadcast_date = date(2026, 10, 2)

    assert fields(state) == {
        "title": "全力！脱力タイムズ <ネプ> & 福本莉子",
        "description": "▼一行目\n▼二行目",
        "published": "2026-10-02",
    }


def test_the_sidecar_drops_characters_xml_forbids(state: ProjectState):
    state.source.title = "タイトル\x0b"
    state.source.description = "一行目\x1f\n二行目\x00"

    assert fields(state) == {"title": "タイトル", "description": "一行目\n二行目"}


def test_the_sidecar_uses_the_researched_date(state: ProjectState):
    state.side_tasks.date_research = DateResearchRecord(
        completed_at=now(),
        elapsed_s=1.0,
        verdict="found",
        broadcast_date=date(2019, 4, 1),
    )
    assert fields(state)["published"] == "2019-04-01"


def test_an_undated_untitled_sidecar_falls_back_to_the_name_then_the_id(
    state: ProjectState,
):
    assert fields(state) == {"title": state.id}
    state.name = "show"
    assert fields(state) == {"title": "show"}
