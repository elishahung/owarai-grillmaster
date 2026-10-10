from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError
from tests.fakes import FakeAgentRunner
from tests.translate.conftest import briefing

from grillmaster.agents.adapters.base import Capability
from grillmaster.agents.errors import AgentOutputError
from grillmaster.agents.task import SchemaOutput
from grillmaster.core.json_artifact import write_model
from grillmaster.core.model_spec import Role
from grillmaster.extras.titles import (
    TASK_NAME,
    TitleSuggestions,
    build_titles_task,
    ensure_titles,
    generate_titles,
    load_titles,
)

if TYPE_CHECKING:
    from pathlib import Path

TITLES = TitleSuggestions.model_validate(
    {
        "titles": [
            {"title": "搭檔末日", "reason": "沿用企劃名"},
            {"title": "控比配對", "reason": "影射配對機制"},
            {"title": "職業修羅場", "reason": "點出現場張力"},
        ]
    }
)
BRIEFING = briefing((1, 3), summary="demo")


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
        cache=cache,
        session_dir=cache.parent / "session",
        enabled=enabled,
    )


@pytest.mark.parametrize(
    "titles",
    [
        ["搭檔末日", "控比配對"],
        ["搭檔末日", "控比配對", "職業修羅場", "第四個"],
        ["搭", "控比配對", "職業修羅場"],
        ["一個太長太長的標題", "控比配對", "職業修羅場"],
    ],
    ids=["too-few", "too-many", "too-short", "too-long"],
)
def test_suggestions_must_be_three_short_titles(titles: list[str]) -> None:
    with pytest.raises(ValidationError):
        TitleSuggestions.model_validate(
            {"titles": [{"title": title, "reason": "r"} for title in titles]}
        )


def test_task_hands_the_briefing_over_as_pre_pass_json(tmp_path: Path) -> None:
    task = build_titles_task(BRIEFING, session_dir=tmp_path / "session")

    assert task.name == TASK_NAME
    assert task.role is Role.UTILITY
    assert task.workdir is None
    assert task.requires == frozenset({Capability.WEB_SEARCH})
    assert task.output == SchemaOutput(TitleSuggestions)
    assert task.instructions.startswith("# 影片標題建議產生規範")
    assert task.prompt.startswith("## pre_pass.json\n\n```json\n")
    body = task.prompt.removeprefix("## pre_pass.json\n\n```json\n").removesuffix(
        "\n```"
    )
    assert json.loads(body) == BRIEFING.prompt_dict()


def test_generate_writes_the_cache(tmp_path: Path, cache: Path) -> None:
    agents = FakeAgentRunner({TASK_NAME: TITLES})

    result = generate_titles(
        agents, BRIEFING, cache=cache, session_dir=tmp_path / "session"
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
