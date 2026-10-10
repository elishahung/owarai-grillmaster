from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pytest
from tests.stages.conftest import complete_side_task

from grillmaster.agents.errors import AgentQuotaError
from grillmaster.core.json_artifact import write_model
from grillmaster.core.stage_key import SideTaskKey, StageKey
from grillmaster.core.talent import Talent
from grillmaster.events.types import PlanKind, SkipReason, StepSkipped
from grillmaster.extras.date_research import TASK_NAME, DateResearchResult
from grillmaster.pipeline.side_tasks import SideTaskManager
from grillmaster.pipeline.steps import UsageCollector
from grillmaster.project.state import DateResearchRecord, SourceInfo, now
from grillmaster.project.store import load_state
from grillmaster.stages.base import RunOptions
from grillmaster.stages.date_research import TASK

if TYPE_CHECKING:
    from tests.fakes import FakeAgentRunner, RecordingSink
    from tests.stages.conftest import MakeContext

    from grillmaster.config.load import LoadedConfig
    from grillmaster.events.bus import EventBus
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState

FOUND = DateResearchResult.model_validate(
    {
        "status": "found",
        "broadcast_date": "2026-02-04",
        "trust": "medium",
        "sources": [
            {
                "url": "https://example.com/program",
                "source_name": "WEBザテレビジョン",
                "evidence_summary": "Same segment and cast.",
            }
        ],
    }
)
UNKNOWN = DateResearchResult(status="unknown")


@pytest.fixture
def script() -> dict[str, object]:
    return {TASK_NAME: FOUND}


def cache(layout: ProjectLayout, result: DateResearchResult) -> None:
    write_model(layout.date_research_result, result)


def test_starts_after_the_metadata_stage() -> None:
    assert TASK.key is SideTaskKey.DATE_RESEARCH
    assert TASK.start_after is StageKey.METADATA


def test_moot_once_dated_or_researched(state: ProjectState) -> None:
    assert not TASK.done(state)
    state.broadcast_date = date(2026, 2, 4)
    assert TASK.done(state)
    state.broadcast_date = None
    state.side_tasks.date_research = DateResearchRecord(
        completed_at=now(), elapsed_s=1.0, verdict="unknown"
    )
    assert TASK.done(state)


def test_the_platform_date_wins_over_the_researched_one(state: ProjectState) -> None:
    state.side_tasks.date_research = DateResearchRecord(
        completed_at=now(),
        elapsed_s=1.0,
        verdict="found",
        broadcast_date=date(2026, 2, 4),
    )
    assert state.effective_broadcast_date == date(2026, 2, 4)
    state.broadcast_date = date(2025, 1, 1)
    assert state.effective_broadcast_date == date(2025, 1, 1)


def test_found_date_is_applied_and_recorded(
    make_context: MakeContext,
    layout: ProjectLayout,
    agents: FakeAgentRunner,
) -> None:
    summary = complete_side_task(TASK, make_context(SideTaskKey.DATE_RESEARCH))

    assert summary == "2026-02-04 (medium trust)"
    saved = load_state(layout)
    # The platform's field stays the platform's; the record holds the find.
    assert saved.broadcast_date is None
    assert saved.effective_broadcast_date == date(2026, 2, 4)
    record = saved.side_tasks.date_research
    assert record is not None
    assert (record.verdict, record.trust, record.elapsed_s) == ("found", "medium", 1.0)
    assert record.broadcast_date == date(2026, 2, 4)
    assert layout.date_research_result.is_file()
    side = layout.side_dir(SideTaskKey.DATE_RESEARCH)
    task = agents.task(TASK_NAME)
    assert task.workdir is None
    assert task.session_dir == side / "session"


def test_unknown_verdict_is_recorded_without_a_date(
    make_context: MakeContext,
    layout: ProjectLayout,
    agents: FakeAgentRunner,
) -> None:
    agents.script[TASK_NAME] = UNKNOWN

    ctx = make_context(SideTaskKey.DATE_RESEARCH)
    assert complete_side_task(TASK, ctx) == "unknown"

    saved = load_state(layout)
    assert saved.effective_broadcast_date is None
    record = saved.side_tasks.date_research
    assert record is not None
    assert (record.verdict, record.trust, record.broadcast_date) == (
        "unknown",
        None,
        None,
    )


def test_research_is_seeded_with_the_project_metadata(
    make_context: MakeContext,
    state: ProjectState,
    agents: FakeAgentRunner,
) -> None:
    state.name = "archive_rerun"
    state.translation_hint = "第2回の続き"
    state.source = SourceInfo(
        title="番組タイトル",
        broadcast_label="2018年放送",
        talents=[Talent(id="t1", name="出演者A", roles=("MC",))],
    )

    TASK.run(make_context(SideTaskKey.DATE_RESEARCH))

    prompt = agents.task(TASK_NAME).prompt
    assert "- Source URL: https://tver.jp/episodes/epabc123" in prompt
    assert "- File name: archive_rerun" in prompt
    assert "- Title: 番組タイトル" in prompt
    assert "- User hint: 第2回の続き" in prompt
    assert (
        '- Platform-stated original broadcast year: 2018 (source label: "2018年放送")'
        in prompt
    )
    assert "- 出演者A (MC)" in prompt


def test_cached_verdict_skips_the_agent(
    make_context: MakeContext,
    layout: ProjectLayout,
    agents: FakeAgentRunner,
) -> None:
    cache(layout, FOUND)

    complete_side_task(TASK, make_context(SideTaskKey.DATE_RESEARCH))

    assert agents.tasks == []
    assert load_state(layout).side_tasks.date_research is not None


def test_agent_failure_leaves_the_task_unrecorded(
    make_context: MakeContext,
    layout: ProjectLayout,
    agents: FakeAgentRunner,
) -> None:
    agents.script[TASK_NAME] = AgentQuotaError("429")

    with pytest.raises(AgentQuotaError):
        TASK.run(make_context(SideTaskKey.DATE_RESEARCH))

    assert load_state(layout).side_tasks.date_research is None


# --- skipped: a paid verdict still applies -------------------------------------


@pytest.mark.parametrize("reason", [SkipReason.DISABLED, SkipReason.BREAKPOINT])
def test_skip_applies_a_paid_verdict(
    make_context: MakeContext, layout: ProjectLayout, reason: SkipReason
) -> None:
    cache(layout, FOUND)

    assert TASK.on_skip is not None
    TASK.on_skip(make_context(SideTaskKey.DATE_RESEARCH), reason)

    saved = load_state(layout)
    assert saved.effective_broadcast_date == date(2026, 2, 4)
    record = saved.side_tasks.date_research
    assert record is not None
    assert (record.verdict, record.elapsed_s, record.agent_usage) == ("found", 0, None)


def test_skip_without_a_verdict_changes_nothing(
    make_context: MakeContext, layout: ProjectLayout
) -> None:
    assert TASK.on_skip is not None
    TASK.on_skip(make_context(SideTaskKey.DATE_RESEARCH), SkipReason.DISABLED)

    assert load_state(layout).side_tasks.date_research is None


@pytest.mark.parametrize("reason", list(SkipReason))
def test_skip_never_overrides_an_existing_date(
    make_context: MakeContext,
    layout: ProjectLayout,
    state: ProjectState,
    reason: SkipReason,
) -> None:
    state.broadcast_date = date(2025, 1, 1)
    cache(layout, FOUND)

    assert TASK.on_skip is not None
    TASK.on_skip(make_context(SideTaskKey.DATE_RESEARCH), reason)

    assert state.broadcast_date == date(2025, 1, 1)
    assert state.side_tasks.date_research is None


@pytest.mark.parametrize(
    ("requested", "break_after", "reason"),
    [
        (False, None, SkipReason.DISABLED),
        (True, StageKey.METADATA, SkipReason.BREAKPOINT),
    ],
    ids=["disabled", "breakpoint"],
)
def test_manager_applies_the_paid_verdict_without_researching(
    *,
    make_context: MakeContext,
    layout: ProjectLayout,
    state: ProjectState,
    loaded: LoadedConfig,
    agents: FakeAgentRunner,
    bus: EventBus,
    recording_sink: RecordingSink,
    requested: bool,
    break_after: StageKey | None,
    reason: SkipReason,
) -> None:
    cache(layout, UNKNOWN)
    run_options = RunOptions(
        source=state.source_id, date_research=requested, break_after=break_after
    )

    with SideTaskManager(
        (TASK,),
        context=lambda task: make_context(task.key),
        options=run_options,
        config=loaded.config,
        events=bus,
        usage=UsageCollector(),
    ) as manager:
        manager.stage_passed(StageKey.METADATA)

    assert agents.tasks == []
    record = load_state(layout).side_tasks.date_research
    assert record is not None
    assert record.verdict == "unknown"
    assert StepSkipped(TASK.key, PlanKind.SIDE_TASK, reason) in recording_sink.events
