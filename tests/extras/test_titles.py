from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError
from tests.fakes import FakeAgentRunner, make_briefing

from grillmaster.agents.adapters.base import Capability
from grillmaster.agents.errors import AgentOutputError
from grillmaster.agents.schema import strict_json_schema
from grillmaster.agents.task import SchemaOutput
from grillmaster.core.json_artifact import write_model
from grillmaster.core.model_spec import Role
from grillmaster.core.talent import Talent
from grillmaster.extras.titles import (
    ANGLE_LABELS,
    TASK_NAME,
    SourceListing,
    TitleSuggestions,
    build_titles_task,
    ensure_titles,
    generate_titles,
    load_titles,
    titles_info,
)

if TYPE_CHECKING:
    from pathlib import Path


DATA: dict[str, Any] = {
    "program": "節目全名",
    "translation": {"title": "新人初登場挑戰", "basis": "官方集名精簡"},
    "project": {"title": "猜拳王", "basis": "官方企劃名短稱"},
    "summary": {"title": "全員猜拳爭獎金", "basis": "官方介紹的設定"},
    "talent": {"title": "甲組合、乙先生", "basis": "官方出演者"},
    "creative": {"title": "出拳前心理戰", "basis": "開場規則延伸"},
}
TITLES = TitleSuggestions.model_validate(DATA)
BRIEFING = make_briefing((1, 3), summary="demo")
SOURCE = SourceListing(
    series="番組",
    channel="局",
    title="番組 集名",
    description="介紹",
    talents=(Talent(id="t1", name="芸人", roles=("ゲスト",)),),
)


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    return tmp_path / "work" / "package" / "titles.json"


@pytest.fixture
def briefing_path(tmp_path: Path) -> Path:
    path = tmp_path / "work" / "08_prepass" / "briefing.json"
    write_model(path, BRIEFING)
    return path


def ensure(
    agents: FakeAgentRunner, *, briefing: Path, cache: Path, enabled: bool = True
) -> TitleSuggestions | None:
    return ensure_titles(
        agents,
        briefing=briefing,
        source=SOURCE,
        cache=cache,
        session_dir=cache.parent / "session",
        enabled=enabled,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"summary": None},
        {"translation": {"title": "搭", "basis": "b"}},
        {"translation": {"title": "官方集名的翻譯剪得還是太長太長了", "basis": "b"}},
        {"project": {"title": "企劃名超過十個字的企劃", "basis": "b"}},
        {"creative": {"title": "創意", "basis": ""}},
    ],
    ids=[
        "missing-angle",
        "too-short",
        "translation-too-long",
        "project-too-long",
        "no-basis",
    ],
)
def test_suggestions_hold_every_angle_within_its_length(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        TitleSuggestions.model_validate({**DATA, **overrides})


def test_every_angle_has_a_label_and_its_bound_reaches_the_schema() -> None:
    schema = strict_json_schema(TitleSuggestions)["properties"]

    assert list(ANGLE_LABELS) == [
        field for field in TitleSuggestions.model_fields if field != "program"
    ]
    project = next(s for s in schema["project"]["anyOf"] if s["type"] == "object")
    bounds = {
        field: (project if field == "project" else schema[field])["properties"][
            "title"
        ]["maxLength"]
        for field in ANGLE_LABELS
    }
    assert bounds == {
        "translation": 14,
        "project": 10,
        "summary": 12,
        "talent": 24,
        "creative": 10,
    }


def test_an_episode_without_a_project_skips_that_angle() -> None:
    suggestions = TitleSuggestions.model_validate({**DATA, "project": None})

    titles = titles_info(suggestions, nickname="暱稱", aired=None)["titles"]

    assert [title["kind"] for title in titles] == ["翻譯", "概述", "藝人", "創意"]


def test_task_hands_the_listing_and_briefing_over(tmp_path: Path) -> None:
    task = build_titles_task(BRIEFING, source=SOURCE, session_dir=tmp_path / "session")

    assert task.name == TASK_NAME
    assert task.role is Role.UTILITY
    assert task.workdir is None
    assert task.requires == frozenset({Capability.WEB_SEARCH})
    assert task.output == SchemaOutput(TitleSuggestions)
    assert task.instructions.startswith("# 影片標題建議產生規範")
    source, briefing = task.prompt.split("\n\n## pre_pass.json\n\n")
    assert source.startswith("## source.json\n\n```json\n")
    assert json.loads(
        source.removeprefix("## source.json\n\n```json\n").removesuffix("\n```")
    ) == {
        "series": "番組",
        "channel": "局",
        "title": "番組 集名",
        "description": "介紹",
        "talents": [{"name": "芸人", "name_kana": None, "roles": ["ゲスト"]}],
    }
    body = briefing.removeprefix("```json\n").removesuffix("\n```")
    assert json.loads(body) == BRIEFING.prompt_dict()


def test_generate_writes_the_cache(tmp_path: Path, cache: Path) -> None:
    agents = FakeAgentRunner({TASK_NAME: TITLES})

    result = generate_titles(
        agents,
        BRIEFING,
        source=SOURCE,
        cache=cache,
        session_dir=tmp_path / "session",
    )

    assert result == TITLES
    assert load_titles(cache) == TITLES


def test_existing_titles_are_reused_without_the_agent(
    briefing_path: Path, cache: Path
) -> None:
    write_model(cache, TITLES)
    agents = FakeAgentRunner()

    # Reused even with suggestion turned off.
    assert ensure(agents, briefing=briefing_path, cache=cache, enabled=False) == TITLES
    assert agents.tasks == []


def test_missing_titles_are_generated_from_the_briefing(
    briefing_path: Path, cache: Path
) -> None:
    agents = FakeAgentRunner({TASK_NAME: TITLES})

    assert ensure(agents, briefing=briefing_path, cache=cache) == TITLES
    assert load_titles(cache) == TITLES
    assert '"summary": "demo"' in agents.task(TASK_NAME).prompt
    assert agents.task(TASK_NAME).session_dir == cache.parent / "session"


def test_disabled_never_generates(briefing_path: Path, cache: Path) -> None:
    agents = FakeAgentRunner()

    assert ensure(agents, briefing=briefing_path, cache=cache, enabled=False) is None
    assert not cache.exists()


def test_unreadable_titles_are_generated_again(
    briefing_path: Path, cache: Path
) -> None:
    cache.parent.mkdir(parents=True)
    cache.write_text("not json", encoding="utf-8")
    agents = FakeAgentRunner({TASK_NAME: TITLES})

    assert ensure(agents, briefing=briefing_path, cache=cache) == TITLES
    assert len(agents.tasks) == 1


def test_missing_briefing_yields_no_titles(tmp_path: Path, cache: Path) -> None:
    agents = FakeAgentRunner()

    assert ensure(agents, briefing=tmp_path / "missing.json", cache=cache) is None
    assert agents.tasks == []


def test_agent_failure_yields_no_titles(briefing_path: Path, cache: Path) -> None:
    agents = FakeAgentRunner({TASK_NAME: AgentOutputError("no valid titles")})

    assert ensure(agents, briefing=briefing_path, cache=cache) is None
    assert not cache.exists()


@pytest.mark.parametrize(
    ("nickname", "aired", "prefix", "suffix"),
    [
        ("暱稱", date(2026, 11, 17), "暱稱", " 261117"),
        (None, date(2026, 11, 17), "節目全名", " 261117"),
        ("暱稱", None, "暱稱", ""),
    ],
    ids=["nickname", "program", "undated"],
)
def test_info_titles_carry_the_angle_content_and_full_title(
    nickname: str | None, aired: date | None, prefix: str, suffix: str
) -> None:
    info = titles_info(TITLES, nickname=nickname, aired=aired)

    assert info == {
        "titles": [
            {
                "kind": kind,
                "title": f"{prefix}:{content}{suffix}",
                "content": content,
                "basis": basis,
            }
            for kind, content, basis in [
                ("翻譯", "新人初登場挑戰", "官方集名精簡"),
                ("企劃", "猜拳王", "官方企劃名短稱"),
                ("概述", "全員猜拳爭獎金", "官方介紹的設定"),
                ("藝人", "甲組合、乙先生", "官方出演者"),
                ("創意", "出拳前心理戰", "開場規則延伸"),
            ]
        ]
    }


def test_titles_in_the_three_title_format_are_regenerated(
    briefing_path: Path, cache: Path
) -> None:
    cache.parent.mkdir(parents=True)
    cache.write_text(
        json.dumps(
            {"program": "節目全名", "titles": [{"title": "舊標題", "reason": "r"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    agents = FakeAgentRunner({TASK_NAME: TITLES})

    assert ensure(agents, briefing=briefing_path, cache=cache) == TITLES
    assert len(agents.tasks) == 1
