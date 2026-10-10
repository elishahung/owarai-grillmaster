from __future__ import annotations

import json
from datetime import date, datetime

import pytest
from pydantic import ValidationError

from grillmaster.core.source_id import Platform, SourceId
from grillmaster.core.stage_key import SideTaskKey, StageKey
from grillmaster.project.state import (
    DateResearchRecord,
    ProjectState,
    Section,
    Talent,
    TaskRecord,
)


def _dump(state: ProjectState) -> dict[str, object]:
    return json.loads(state.model_dump_json())


def test_create_records_identity_and_an_aware_timestamp():
    state = ProjectState.create(
        SourceId(Platform.YOUTUBE, "v=abc"), translation_hint="漫才"
    )
    assert (state.id, state.platform, state.translation_hint) == (
        "v=abc",
        Platform.YOUTUBE,
        "漫才",
    )
    assert state.created_at.tzinfo is not None
    assert state.source_id == SourceId(Platform.YOUTUBE, "v=abc")
    assert state.stages == {}
    assert state.name is None


def test_full_state_round_trips_through_json(state: ProjectState):
    state.name = "全力脱力タイムズ"
    state.broadcast_date = date(2026, 10, 9)
    state.source.series = "全力!脱力タイムズ"
    state.source.talents = [Talent(id="t1", name="有吉弘行", roles=("MC",))]
    state.section = Section(start=10.0, end=95.5)
    state.add_asr_cost(0.31)
    state.mark_done(
        StageKey.PREPASS,
        elapsed_s=412.0,
        params={"model": "agy/gemini-3.1-pro/high"},
        agent_usage={"input_tokens": 1200},
    )
    state.side_tasks.date_research = DateResearchRecord(
        completed_at=datetime(2026, 10, 9, 12).astimezone(),
        elapsed_s=30.0,
        verdict="unknown",
    )

    assert ProjectState.model_validate_json(state.model_dump_json()) == state
    assert _dump(state)["stages"] == {
        "prepass": {
            "completed_at": state.stages[StageKey.PREPASS].completed_at.isoformat(),
            "elapsed_s": 412.0,
            "params": {"model": "agy/gemini-3.1-pro/high"},
            "agent_usage": {"input_tokens": 1200},
        }
    }


def test_unknown_ledger_key_fails(state: ProjectState):
    data = _dump(state)
    data["stages"] = {
        "translate": {"completed_at": "2026-10-10T00:00:00+08:00", "elapsed_s": 1}
    }
    with pytest.raises(ValidationError):
        ProjectState.model_validate(data)


@pytest.mark.parametrize(
    "extra",
    [{"is_asr_completed": True}, {"asr_cost": 0.1}, {"parent_project_path": None}],
    ids=["legacy-flag", "legacy-cost", "legacy-parent"],
)
def test_unknown_top_level_fields_fail(state: ProjectState, extra: dict[str, object]):
    with pytest.raises(ValidationError):
        ProjectState.model_validate(_dump(state) | extra)


def test_naive_timestamps_are_rejected(state: ProjectState):
    with pytest.raises(ValidationError):
        state.mark_done(
            StageKey.ASR,
            elapsed_s=1.0,
            completed_at=datetime(2026, 10, 10),  # noqa: DTZ001
        )


def test_mark_done_then_clear(state: ProjectState):
    state.mark_done(StageKey.METADATA, elapsed_s=2.1)
    state.mark_done(StageKey.DOWNLOAD, elapsed_s=30.0)
    assert state.is_done(StageKey.METADATA)
    assert not state.is_done(StageKey.ASR)

    state.clear([StageKey.DOWNLOAD, StageKey.ASR])
    assert list(state.stages) == [StageKey.METADATA]


def test_mark_done_replaces_and_snapshots_params(state: ProjectState):
    params = {"model": "codex/gpt-5"}
    state.mark_done(StageKey.REFINE, elapsed_s=1.0, params=params)
    params["model"] = "changed"
    state.mark_done(StageKey.REFINE, elapsed_s=5.0, params={"model": "claude/opus"})

    record = state.stages[StageKey.REFINE]
    assert (record.elapsed_s, record.params, record.agent_usage) == (
        5.0,
        {"model": "claude/opus"},
        None,
    )


def test_asr_cost_accumulates_and_rejects_negatives(state: ProjectState):
    state.add_asr_cost(0.25)
    state.add_asr_cost(0.0)
    state.add_asr_cost(0.5)
    assert state.asr_cost_usd == pytest.approx(0.75)
    with pytest.raises(ValueError, match="non-negative"):
        state.add_asr_cost(-0.1)


@pytest.mark.parametrize(
    ("start", "end"),
    [(10.0, 10.0), (20.0, 5.0), (-1.0, None)],
    ids=["empty", "reversed", "negative"],
)
def test_invalid_sections_fail(start: float, end: float | None):
    with pytest.raises(ValidationError):
        Section(start=start, end=end)


def test_open_ended_sections_are_valid():
    assert Section(start=30.0).end is None
    assert Section(end=60.0).start is None


def test_assignment_is_validated(state: ProjectState):
    with pytest.raises(ValidationError):
        state.platform = "niconico"  # pyright: ignore[reportAttributeAccessIssue] - an invalid value on purpose


def test_side_task_completion(state: ProjectState):
    assert not state.side_tasks.is_done(SideTaskKey.COVER)
    state.side_tasks.cover = TaskRecord(
        completed_at=datetime(2026, 10, 9).astimezone(), elapsed_s=1
    )
    assert state.side_tasks.is_done(SideTaskKey.COVER)
    assert not state.side_tasks.is_done(SideTaskKey.DATE_RESEARCH)


def test_date_research_verdict_is_closed():
    with pytest.raises(ValidationError):
        DateResearchRecord.model_validate(
            {
                "completed_at": "2026-10-10T00:00:00+08:00",
                "elapsed_s": 1,
                "verdict": "maybe",
            }
        )
