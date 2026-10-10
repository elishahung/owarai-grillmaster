from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError
from tests.fakes import FakeAgentRunner

from grillmaster.agents.adapters.base import Capability
from grillmaster.agents.task import SchemaOutput
from grillmaster.core.model_spec import Role
from grillmaster.extras.date_research import (
    TASK_NAME,
    DateResearchResult,
    ResearchContext,
    ResearchTalent,
    adopted_date,
    build_date_research_task,
    load_cached_result,
    render_context,
    research_broadcast_date,
)

if TYPE_CHECKING:
    from pathlib import Path

FOUND = {
    "status": "found",
    "broadcast_date": "2026-02-04",
    "trust": "high",
    "sources": [
        {
            "url": "https://example.com/program",
            "source_name": "Apple TV",
            "evidence_summary": "Episode title and program match.",
        }
    ],
    "rejected_candidates": [
        {"date": "2026-02-05", "reason": "BiliBili upload date, not broadcast date"}
    ],
}


def found(trust: str = "high") -> DateResearchResult:
    return DateResearchResult.model_validate({**FOUND, "trust": trust})


UNKNOWN = DateResearchResult(status="unknown")

CONTEXT = ResearchContext(
    platform="tver",
    source_url="https://tver.jp/episodes/ep123",
    video_id="ep123",
    file_name="260202_variety_show",
)


@pytest.fixture
def result_path(tmp_path: Path) -> Path:
    return tmp_path / "side" / "date_research" / "result.json"


def research(agents: FakeAgentRunner, result_path: Path):
    return research_broadcast_date(
        agents,
        CONTEXT,
        result_path=result_path,
        session_dir=result_path.parent / "session",
    )


# --- verdict schema -----------------------------------------------------------


def test_found_without_date_is_rejected() -> None:
    with pytest.raises(ValidationError, match="broadcast_date is required"):
        DateResearchResult.model_validate({"status": "found", "trust": "high"})


def test_found_without_trust_is_rejected() -> None:
    with pytest.raises(ValidationError, match="trust is required"):
        DateResearchResult.model_validate(
            {"status": "found", "broadcast_date": "2026-02-04"}
        )


def test_unknown_needs_no_date_or_trust() -> None:
    result = DateResearchResult.model_validate({"status": "unknown"})

    assert result.broadcast_date is None
    assert result.trust is None


# --- task ---------------------------------------------------------------------


def test_task_searches_the_web_from_a_throwaway_directory(tmp_path: Path) -> None:
    task = build_date_research_task(CONTEXT, session_dir=tmp_path / "session")

    assert task.name == TASK_NAME
    assert task.role is Role.UTILITY
    assert task.workdir is None
    assert task.requires == frozenset({Capability.WEB_SEARCH})
    assert task.output == SchemaOutput(DateResearchResult)
    assert task.instructions.startswith("Research the original Japanese broadcast")
    assert task.prompt == render_context(CONTEXT)


def test_context_renders_every_known_field() -> None:
    context = ResearchContext(
        platform="tver",
        source_url="https://tver.jp/episodes/ep123",
        video_id="ep123",
        file_name="260202_variety_show",
        title="番組タイトル",
        description="企画の説明",
        hint="第2回の続き",
        talents=(
            ResearchTalent("出演者A", name_kana="しゅつえんしゃ", roles=("MC",)),
            ResearchTalent("出演者B"),
        ),
    )

    assert render_context(context).splitlines() == [
        "## Project context",
        "",
        "- Platform: tver",
        "- Source URL: https://tver.jp/episodes/ep123",
        "- Video ID: ep123",
        "- File name: 260202_variety_show",
        "- Title: 番組タイトル",
        "- Description: 企画の説明",
        "- User hint: 第2回の続き",
        "- Official source cast/talent metadata:",
        "- 出演者A / しゅつえんしゃ (MC)",
        "- 出演者B",
    ]


def test_context_omits_unknown_fields() -> None:
    text = render_context(CONTEXT)

    assert "Title" not in text
    assert "User hint" not in text
    assert "Platform-stated original broadcast year" not in text


def test_context_states_the_archive_broadcast_year() -> None:
    context = ResearchContext(
        platform="tver",
        source_url="https://tver.jp/episodes/ep123",
        video_id="ep123",
        broadcast_year=2018,
        broadcast_label="2018年放送",
    )

    assert (
        '- Platform-stated original broadcast year: 2018 (source label: "2018年放送")'
        in render_context(context)
    )


# --- research and cache -------------------------------------------------------


@pytest.mark.parametrize("verdict", [found(), UNKNOWN], ids=["found", "unknown"])
def test_research_writes_the_verdict(
    result_path: Path, verdict: DateResearchResult
) -> None:
    agents = FakeAgentRunner({TASK_NAME: verdict})

    assert research(agents, result_path) == verdict
    assert load_cached_result(result_path) == verdict
    assert agents.task(TASK_NAME).session_dir == result_path.parent / "session"


def test_existing_verdict_skips_the_agent(result_path: Path) -> None:
    result_path.parent.mkdir(parents=True)
    result_path.write_text(found().model_dump_json(), encoding="utf-8")
    agents = FakeAgentRunner()

    result = research(agents, result_path)

    assert agents.tasks == []
    assert result.broadcast_date == date(2026, 2, 4)


def test_corrupt_verdict_is_researched_again(result_path: Path) -> None:
    result_path.parent.mkdir(parents=True)
    result_path.write_text("{ truncated", encoding="utf-8")
    agents = FakeAgentRunner({TASK_NAME: found()})

    research(agents, result_path)

    assert len(agents.tasks) == 1
    assert load_cached_result(result_path) == found()


def test_load_cached_result_misses_on_missing_or_corrupt(result_path: Path) -> None:
    assert load_cached_result(result_path) is None
    result_path.parent.mkdir(parents=True)
    result_path.write_text("not json", encoding="utf-8")
    assert load_cached_result(result_path) is None


# --- adoption -----------------------------------------------------------------


@pytest.mark.parametrize("trust", ["high", "medium", "low"])
def test_any_found_date_is_adopted(result_path: Path, trust: str) -> None:
    assert adopted_date(found(trust), evidence=result_path) == date(2026, 2, 4)


def test_unknown_adopts_nothing(result_path: Path) -> None:
    assert adopted_date(UNKNOWN, evidence=result_path) is None
