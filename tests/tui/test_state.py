from __future__ import annotations

import pytest
from tests.tui.fakes import PLAN, FakeClock

from grillmaster.events.types import (
    ActivityKind,
    AgentActivity,
    AgentSessionFinished,
    AgentSessionStarted,
    BatchItemStarted,
    LogLine,
    PlanKind,
    ProgressAdvanced,
    ProgressFinished,
    ProgressStarted,
    RunFinished,
    RunOutcome,
    RunStarted,
    SessionOutcome,
    SkipReason,
    StepCompleted,
    StepFailed,
    StepSkipped,
    StepStarted,
)
from grillmaster.tui.state import (
    ItemState,
    LogEntry,
    PipelineState,
    RingLog,
    SessionState,
    chunk_range,
)

STAGE = PlanKind.STAGE


def _step(state: PipelineState, key: str):
    step = state.step(key)
    assert step is not None
    return step


def _session_started(task: str, stage: str | None = "chunks") -> AgentSessionStarted:
    return AgentSessionStarted(task, stage, "agy", "gemini-3.1-pro", None)


# -- plan ----------------------------------------------------------------------


def test_run_started_builds_rows_from_the_plan(run_state: PipelineState):
    assert run_state.project == "epabc123"
    assert [step.key for step in run_state.steps] == [entry.key for entry in PLAN]
    assert _step(run_state, "refine").state is ItemState.DISABLED
    assert _step(run_state, "metadata").state is ItemState.PENDING
    chunks = _step(run_state, "chunks")
    assert chunks.weight == 12
    assert chunks.params == {"model": "agy/gemini-3.1-pro"}


def test_display_order_groups_stages_delivery_then_side_tasks():
    state = PipelineState()
    # The pipeline plans side tasks last already; shuffle to prove the sort.
    state.apply(RunStarted("p", (PLAN[-1], PLAN[5], PLAN[0])))
    assert [step.key for step in state.display_steps()] == [
        "metadata",
        "package",
        "date_research",
    ]


# -- steps ---------------------------------------------------------------------


def test_stage_lifecycle(run_state: PipelineState, clock: FakeClock):
    run_state.apply(StepSkipped("metadata", STAGE, SkipReason.ALREADY_COMPLETE))
    run_state.apply(StepStarted("download", STAGE), at=110.0)
    download = _step(run_state, "download")
    assert download.state is ItemState.RUNNING
    assert run_state.current_step_key == "download"
    clock.now = 115.0
    assert download.live_elapsed(run_state.now()) == pytest.approx(5.0)

    run_state.apply(StepCompleted("download", STAGE, 12.5, "video.mp4"))

    assert _step(run_state, "metadata").state is ItemState.CACHED
    assert download.state is ItemState.DONE
    assert download.elapsed == 12.5
    assert download.result == "video.mp4"
    assert run_state.current_step_key is None


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (SkipReason.ALREADY_COMPLETE, ItemState.CACHED),
        (SkipReason.DISABLED, ItemState.DISABLED),
        (SkipReason.BREAKPOINT, ItemState.SKIPPED),
    ],
)
def test_skip_reasons(
    run_state: PipelineState, reason: SkipReason, expected: ItemState
):
    run_state.apply(StepSkipped("cover", PlanKind.SIDE_TASK, reason))
    assert _step(run_state, "cover").state is expected


def test_step_failed_records_error_and_elapsed(run_state: PipelineState):
    run_state.apply(StepStarted("cover", PlanKind.SIDE_TASK), at=100.0)
    run_state.apply(StepFailed("cover", PlanKind.SIDE_TASK, "codex died"), at=103.0)
    cover = _step(run_state, "cover")
    assert cover.state is ItemState.FAILED
    assert cover.error == "codex died"
    assert cover.elapsed == pytest.approx(3.0)


def test_side_task_never_becomes_the_current_step(run_state: PipelineState):
    run_state.apply(StepStarted("download", STAGE))
    run_state.apply(StepStarted("cover", PlanKind.SIDE_TASK))
    run_state.apply(StepCompleted("cover", PlanKind.SIDE_TASK, 1.0))
    assert run_state.current_step_key == "download"


def test_step_outside_the_plan_still_gets_a_row(run_state: PipelineState):
    run_state.apply(StepStarted("surprise", PlanKind.DELIVERY))
    assert _step(run_state, "surprise").state is ItemState.RUNNING


def test_run_failure_marks_the_running_step(run_state: PipelineState):
    run_state.apply(StepStarted("download", STAGE))
    run_state.apply(RunFinished(RunOutcome.FAILED, "boom"))
    download = _step(run_state, "download")
    assert download.state is ItemState.FAILED
    assert download.error == "boom"
    assert run_state.run_outcome is RunOutcome.FAILED
    # The work has not returned yet: the dashboard keeps running.
    assert not run_state.finished


def test_work_finished_ends_the_dashboard(run_state: PipelineState, clock: FakeClock):
    run_state.apply(StepStarted("download", STAGE))
    run_state.work_finished("abema flake", at=130.0)
    clock.now = 500.0
    assert run_state.finished
    assert run_state.failed
    assert run_state.error == "abema flake"
    assert _step(run_state, "download").state is ItemState.FAILED
    assert run_state.wall_elapsed() == pytest.approx(30.0)


def test_successful_work_finishes_without_failure(run_state: PipelineState):
    run_state.apply(RunFinished(RunOutcome.COMPLETED))
    run_state.work_finished(None)
    assert run_state.finished
    assert not run_state.failed


def test_retry_clears_the_failure_and_the_next_run_rebuilds(run_state: PipelineState):
    run_state.apply(StepStarted("chunks", STAGE))
    run_state.apply(_session_started("chunks/0001-0040"))
    run_state.work_finished("boom")

    run_state.reset_for_retry()
    assert not run_state.finished
    assert not run_state.failed
    assert run_state.error is None

    run_state.apply(RunStarted("epabc123", PLAN))
    assert _step(run_state, "chunks").state is ItemState.PENDING
    assert run_state.sessions == {}


def test_batch_rolls_over_per_project(run_state: PipelineState):
    run_state.apply(BatchItemStarted(1, 2, "BV1"))
    run_state.apply(StepCompleted("download", STAGE, 1.0))
    run_state.apply(RunFinished(RunOutcome.COMPLETED))
    run_state.apply(BatchItemStarted(2, 2, "BV2"))
    run_state.apply(RunStarted("BV2", PLAN))

    assert run_state.batch == (2, 2)
    assert run_state.project == "BV2"
    assert _step(run_state, "download").state is ItemState.PENDING
    assert not run_state.finished
    assert [entry.text for entry in run_state.pipeline_log] == [
        "Serial 1/2: BV1",
        "Serial 2/2: BV2",
    ]


# -- progress ------------------------------------------------------------------


def test_progress_scope_is_owned_by_the_emitting_step(run_state: PipelineState):
    run_state.apply(StepStarted("download", STAGE))
    run_state.apply(StepStarted("cover", PlanKind.SIDE_TASK))
    run_state.apply(ProgressStarted("dl", "Downloading 0.mp4", 1.0), stage="download")
    run_state.apply(ProgressStarted("burn", "Rendering", 10.0), stage="cover")
    run_state.apply(ProgressAdvanced("dl", 0.5, "1.2MiB/s"))
    run_state.apply(ProgressAdvanced("burn", 2.0))

    dl = _step(run_state, "download").bars["dl"]
    assert dl.fraction == pytest.approx(0.5)
    assert dl.note == "1.2MiB/s"
    assert _step(run_state, "cover").bars["burn"].fraction == pytest.approx(0.2)

    run_state.apply(ProgressFinished("dl"))
    assert dl.done
    assert dl.fraction == 1.0


def test_unscoped_progress_falls_back_to_the_running_stage(run_state: PipelineState):
    run_state.apply(StepStarted("download", STAGE))
    run_state.apply(ProgressStarted("dl", "Downloading", None))
    bar = _step(run_state, "download").bars["dl"]
    assert bar.fraction is None  # indeterminate


def test_progress_without_owner_or_scope_is_ignored(run_state: PipelineState):
    run_state.apply(ProgressStarted("orphan", "nobody", 1.0))
    run_state.apply(ProgressAdvanced("never-started", 1.0))
    run_state.apply(ProgressFinished("never-started"))
    assert all(not step.bars for step in run_state.steps)


def test_total_progress_weights_steps_and_skips_disabled_and_side_tasks(
    run_state: PipelineState,
):
    for key in ("metadata", "download", "chunks", "finalize"):
        run_state.apply(StepSkipped(key, STAGE, SkipReason.ALREADY_COMPLETE))
    run_state.apply(StepStarted("package", PlanKind.DELIVERY))
    run_state.apply(ProgressStarted("render", "Rendering", 4.0), stage="package")
    run_state.apply(ProgressAdvanced("render", 2.0))
    # Weights: stages 1+4+12+1 done, package 4 at half, archive 1 pending;
    # refine (disabled) and the side tasks do not count.
    assert run_state.total_progress() == pytest.approx((18 + 2) / 23)


def test_total_progress_ignores_breakpoint_skips(run_state: PipelineState):
    for key in ("metadata", "download", "chunks", "finalize"):
        run_state.apply(StepCompleted(key, STAGE, 1.0))
    for key in ("package", "archive"):
        run_state.apply(StepSkipped(key, PlanKind.DELIVERY, SkipReason.BREAKPOINT))
    assert run_state.total_progress() == pytest.approx(1.0)


# -- agent sessions --------------------------------------------------------------


def test_session_tracks_activity_tools_and_repairs(run_state: PipelineState, clock):
    run_state.apply(StepStarted("refine", STAGE))
    run_state.apply(
        AgentSessionStarted("refine", "refine", "codex", "gpt-5.5", "medium"), at=100.0
    )
    run_state.apply(AgentActivity("refine", ActivityKind.THOUGHT, "checking names"))
    run_state.apply(AgentActivity("refine", ActivityKind.TOOL_CALL, "get_frames 62.5"))
    run_state.apply(AgentActivity("refine", ActivityKind.TOOL_RESULT, "get_frames ok"))
    run_state.apply(AgentActivity("refine", ActivityKind.REPAIR, "block 12 empty"))

    session = run_state.sessions["refine"]
    assert session.spec == "codex/gpt-5.5/medium"
    assert session.tool_calls == 1
    assert session.repairs == 1
    last = session.last_activity
    assert last is not None
    assert last.summary == "block 12 empty"
    clock.now = 160.0
    assert session.live_elapsed(run_state.now()) == pytest.approx(60.0)

    run_state.apply(
        AgentSessionFinished(
            "refine",
            SessionOutcome.OK,
            61.0,
            1,
            {"input_tokens": 900, "output_tokens": 90},
        )
    )
    assert session.state is SessionState.OK
    assert session.elapsed == 61.0
    assert run_state.sessions_for("refine") == [session]
    assert run_state.total_usage() == {"input_tokens": 900, "output_tokens": 90}
    assert next(iter(run_state.activity)).summary == "checking names"


def test_session_attempts_accumulate_on_retry(run_state: PipelineState):
    task = "chunks/0001-0040"
    run_state.apply(_session_started(task))
    run_state.apply(AgentSessionFinished(task, SessionOutcome.TRANSIENT_ERROR, 5.0, 2))
    run_state.apply(_session_started(task), at=200.0)

    session = run_state.sessions[task]
    assert session.state is SessionState.RUNNING
    assert session.attempts == 2
    assert session.retries == 1
    assert session.started_at == 200.0

    run_state.apply(AgentSessionFinished(task, SessionOutcome.OK, 7.0, 1))
    assert session.repairs == 3
    assert session.outcome is SessionOutcome.OK


def test_session_without_stage_takes_the_emitting_scope(run_state: PipelineState):
    run_state.apply(_session_started("cover", stage=None), stage="cover")
    assert run_state.sessions["cover"].stage == "cover"


def test_activity_for_an_unknown_task_still_reaches_the_activity_log(
    run_state: PipelineState,
):
    run_state.apply(AgentActivity("ghost", ActivityKind.MESSAGE, "120 chars"))
    assert len(run_state.activity) == 1
    assert run_state.sessions == {}


# -- chunk board -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        ("chunks/0001-0119", (1, 119)),
        ("chunks/0120-0240", (120, 240)),
        ("chunks", None),
        ("refine", None),
        ("chunks/0001-0119/fix", None),
    ],
)
def test_chunk_range(task: str, expected: tuple[int, int] | None):
    assert chunk_range(task) == expected


def test_a_cancelled_session_reads_as_cancelled_not_failed(run_state: PipelineState):
    run_state.apply(StepStarted("chunks", STAGE))
    run_state.apply(_session_started("chunks/0001-0040"))
    run_state.apply(_session_started("chunks/0041-0080"))
    run_state.apply(
        AgentSessionFinished("chunks/0001-0040", SessionOutcome.CANCELLED, 1.0, 0)
    )
    run_state.apply(
        AgentSessionFinished("chunks/0041-0080", SessionOutcome.QUOTA_ERROR, 1.0, 0)
    )

    cancelled = run_state.sessions["chunks/0001-0040"]
    assert cancelled.state is SessionState.CANCELLED
    assert cancelled.outcome is SessionOutcome.CANCELLED
    assert run_state.sessions["chunks/0041-0080"].state is SessionState.FAILED
    stats = run_state.chunk_stats("chunks")
    assert (stats.active, stats.failed) == (0, 1)


def test_chunk_board_is_derived_from_chunk_sessions(run_state: PipelineState):
    run_state.apply(StepStarted("chunks", STAGE))
    for task in ("chunks/0081-0120", "chunks/0001-0040", "chunks/0041-0080"):
        run_state.apply(_session_started(task))
    run_state.apply(_session_started("prepass", stage="chunks"))  # not a chunk
    run_state.apply(AgentSessionFinished("chunks/0001-0040", SessionOutcome.OK, 1.0, 0))
    run_state.apply(
        AgentSessionFinished("chunks/0041-0080", SessionOutcome.OUTPUT_ERROR, 1.0, 2)
    )
    run_state.apply(_session_started("chunks/0041-0080"))
    run_state.apply(
        AgentSessionFinished("chunks/0041-0080", SessionOutcome.OUTPUT_ERROR, 1.0, 2)
    )

    cells = run_state.chunk_cells("chunks")
    assert [(cell.from_index, cell.to_index) for cell in cells] == [
        (1, 40),
        (41, 80),
        (81, 120),
    ]
    stats = run_state.chunk_stats("chunks")
    assert (stats.total, stats.done, stats.active, stats.failed, stats.retries) == (
        3,
        1,
        1,
        1,
        1,
    )
    assert run_state.step_progress(_step(run_state, "chunks")) == pytest.approx(1 / 3)
    assert run_state.chunk_cells("refine") == []


def test_chunk_progress_counts_cache_hits_without_cells(run_state: PipelineState):
    run_state.apply(StepStarted("chunks", STAGE))
    run_state.apply(ProgressStarted("chunks", "chunks", 5), stage="chunks")
    # Three chunks come from their cache: progress, but no session.
    for _ in range(3):
        run_state.apply(ProgressAdvanced("chunks"), stage="chunks")
    run_state.apply(_session_started("chunks/0161-0200"))
    run_state.apply(AgentSessionFinished("chunks/0161-0200", SessionOutcome.OK, 1.0, 0))
    run_state.apply(ProgressAdvanced("chunks"), stage="chunks")
    run_state.apply(_session_started("chunks/0201-0240"))

    stats = run_state.chunk_stats("chunks")
    assert (stats.total, stats.done, stats.active) == (5, 4, 1)
    assert len(run_state.chunk_cells("chunks")) == 2
    assert run_state.step_progress(_step(run_state, "chunks")) == pytest.approx(4 / 5)

    # A failed batch leaves its bar open: the board keeps the real count.
    run_state.apply(StepFailed("chunks", STAGE, "quota spent"))
    stats = run_state.chunk_stats("chunks")
    assert (stats.total, stats.done) == (5, 4)


def test_an_all_cached_chunk_stage_still_has_a_board(run_state: PipelineState):
    run_state.apply(StepStarted("chunks", STAGE))
    run_state.apply(ProgressStarted("chunks", "chunks", 2), stage="chunks")
    run_state.apply(ProgressAdvanced("chunks", 2), stage="chunks")
    assert run_state.chunk_cells("chunks") == []
    stats = run_state.chunk_stats("chunks")
    assert (stats.total, stats.done) == (2, 2)


# -- logs --------------------------------------------------------------------------


def test_log_lines_route_to_step_session_and_pipeline_logs(run_state: PipelineState):
    run_state.apply(_session_started("chunks/0001-0040"))
    run_state.apply(LogLine("INFO", "before any step"))
    run_state.apply(LogLine("WARNING", "chunk line", "chunks", "chunks/0001-0040"))
    run_state.apply(LogLine("INFO", "unknown step", "nope"))

    assert list(_step(run_state, "chunks").log) == [LogEntry("WARNING", "chunk line")]
    assert list(run_state.sessions["chunks/0001-0040"].log) == [
        LogEntry("WARNING", "chunk line")
    ]
    assert [entry.text for entry in run_state.pipeline_log] == [
        "before any step",
        "unknown step",
    ]


def test_ring_log_hands_out_only_unseen_lines():
    log = RingLog[int](3)
    for value in range(2):
        log.append(value)
    assert log.since(0) == [0, 1]
    seen = log.count
    for value in range(2, 7):
        log.append(value)
    # Five new lines, three kept.
    assert log.since(seen) == [4, 5, 6]
    assert log.since(log.count) == []
    assert log.last() == 6


def test_session_span_is_parsed_once_at_session_start(run_state: PipelineState):
    run_state.apply(_session_started("chunks/0041-0080"))
    run_state.apply(_session_started("prepass", stage="chunks"))
    assert run_state.sessions["chunks/0041-0080"].span == (41, 80)
    assert run_state.sessions["prepass"].span is None


def test_version_moves_with_every_change(run_state: PipelineState):
    seen = run_state.version
    run_state.apply(LogLine("INFO", "hello"))
    assert run_state.version == seen + 1
    run_state.work_finished("boom")
    assert run_state.version == seen + 2
    run_state.reset_for_retry()
    assert run_state.version == seen + 3
