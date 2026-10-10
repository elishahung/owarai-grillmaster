from __future__ import annotations

import json
import shutil
import threading
import time
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING

import pytest
from loguru import logger
from tests.pipeline.fakes import (
    Journal,
    StageFailedError,
    failing,
    fake_delivery,
    fake_side_task,
    fake_stage,
    ticking_clock,
)

from grillmaster.core.source_id import Platform, SourceId
from grillmaster.core.stage_key import SideTaskKey, StageKey
from grillmaster.events.context import current_stage
from grillmaster.events.types import (
    AgentSessionFinished,
    PlanEntry,
    PlanKind,
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
from grillmaster.pipeline.registry import Pipeline
from grillmaster.pipeline.runner import run_pipeline, run_project
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.state import TaskRecord, now
from grillmaster.project.store import load_state, save_state
from grillmaster.stages.base import RunOptions

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tests.fakes import RecordingSink

    from grillmaster.agents.runner import AgentRunner
    from grillmaster.config.load import LoadedConfig
    from grillmaster.config.model import AppConfig
    from grillmaster.config.secrets import Secrets
    from grillmaster.events.bus import EventBus
    from grillmaster.events.types import Event
    from grillmaster.pipeline.delivery import Archive
    from grillmaster.project.state import ProjectState
    from grillmaster.stages.base import Externals, StageContext

STARTED_AT = datetime(2026, 10, 10, 12, 0, 0).astimezone()

type Runner = Callable[..., ProjectLayout]


@pytest.fixture
def journal() -> Journal:
    return Journal()


@pytest.fixture
def run(
    *,
    layout: ProjectLayout,
    state: ProjectState,
    loaded: LoadedConfig,
    options: RunOptions,
    agents: AgentRunner,
    bus: EventBus,
    externals: Externals,
) -> Runner:
    """Run `pipeline` on the fixture project; keyword overrides for options."""

    def runner(
        pipeline: Pipeline, *, archive: Archive | None = None, **overrides: object
    ) -> ProjectLayout:
        return run_pipeline(
            layout,
            state,
            loaded=loaded,
            options=replace(options, **overrides),
            agents=agents,
            externals=externals,
            events=bus,
            pipeline=pipeline,
            archive=archive,
            clock=ticking_clock(),
            started_at=STARTED_AT,
        )

    return runner


@pytest.fixture
def archived(tmp_path: Path) -> ProjectLayout:
    return ProjectLayout(tmp_path / "archive" / "251009_epabc123")


@pytest.fixture
def archive(archived: ProjectLayout) -> Archive:
    """Moves the project to `archived`, as the real archive step does."""

    def move(layout: ProjectLayout) -> ProjectLayout:
        logger.info("moving")
        archived.root.parent.mkdir(parents=True)
        shutil.move(layout.root, archived.root)
        return archived

    return move


def steps(events: list[Event]) -> list[Event]:
    return [
        event
        for event in events
        if isinstance(event, StepStarted | StepCompleted | StepSkipped | StepFailed)
    ]


def cover_pipeline(
    journal: Journal,
    *,
    action: Callable[[StageContext], str | None] | None = None,
    enabled: bool = True,
) -> Pipeline:
    """Metadata, then a cover side task starting after it."""
    cover = fake_side_task(
        SideTaskKey.COVER, StageKey.METADATA, journal, action=action, enabled=enabled
    )
    return Pipeline((fake_stage(StageKey.METADATA, journal),), side_tasks=(cover,))


def test_runs_stages_in_order_and_records_the_ledger(
    run: Runner, journal: Journal, layout: ProjectLayout
):
    pipeline = Pipeline(
        (
            fake_stage(StageKey.METADATA, journal, params={"official_cc": "on"}),
            fake_stage(StageKey.DOWNLOAD, journal),
        )
    )
    assert run(pipeline) == layout
    assert journal.entries == ["run:metadata@metadata", "run:download@download"]
    saved = load_state(layout)
    assert list(saved.stages) == [StageKey.METADATA, StageKey.DOWNLOAD]
    assert saved.stages[StageKey.METADATA].params == {"official_cc": "on"}
    assert saved.stages[StageKey.METADATA].elapsed_s == 1.0
    assert saved.stages[StageKey.METADATA].agent_usage is None


def test_event_sequence(
    run: Runner,
    journal: Journal,
    recording_sink: RecordingSink,
    archive: Archive,
    archived: ProjectLayout,
):
    pipeline = Pipeline(
        (
            fake_stage(StageKey.METADATA, journal, enabled=False),
            fake_stage(StageKey.DOWNLOAD, journal, params={"tool": "yt-dlp"}),
        ),
        delivery=(fake_delivery("package", journal),),
    )
    run(pipeline, archive=archive)
    assert recording_sink.events == [
        RunStarted(
            "epabc123",
            (
                PlanEntry("metadata", "Stage metadata", PlanKind.STAGE, False, {}, 1),
                PlanEntry(
                    "download",
                    "Stage download",
                    PlanKind.STAGE,
                    True,
                    {"tool": "yt-dlp"},
                    2,
                ),
                PlanEntry("package", "Deliver package", PlanKind.DELIVERY, True, {}, 3),
                PlanEntry("archive", "Archive", PlanKind.DELIVERY, True, {}, 1),
            ),
        ),
        StepSkipped("metadata", PlanKind.STAGE, SkipReason.DISABLED),
        StepStarted("download", PlanKind.STAGE),
        StepCompleted("download", PlanKind.STAGE, 1.0),
        StepStarted("package", PlanKind.DELIVERY),
        StepCompleted("package", PlanKind.DELIVERY, 1.0),
        StepStarted("archive", PlanKind.DELIVERY),
        StepCompleted("archive", PlanKind.DELIVERY, 1.0, str(archived.root)),
        RunFinished(RunOutcome.COMPLETED),
    ]


def test_a_stage_result_reaches_its_completion_event(
    run: Runner, journal: Journal, recording_sink: RecordingSink
):
    stage = fake_stage(StageKey.ASR, journal, action=lambda ctx: "$0.4000")

    run(Pipeline((stage,)))

    assert StepCompleted("asr", PlanKind.STAGE, 1.0, "$0.4000") in recording_sink.events


def test_resume_skips_completed_stages(
    run: Runner,
    journal: Journal,
    state: ProjectState,
    recording_sink: RecordingSink,
):
    state.mark_done(StageKey.METADATA, elapsed_s=3.0)
    pipeline = Pipeline(
        (fake_stage(StageKey.METADATA, journal), fake_stage(StageKey.DOWNLOAD, journal))
    )
    run(pipeline)
    assert journal.entries == ["on_skip:metadata", "run:download@download"]
    assert steps(recording_sink.events)[0] == StepSkipped(
        "metadata", PlanKind.STAGE, SkipReason.ALREADY_COMPLETE
    )
    assert state.stages[StageKey.METADATA].elapsed_s == 3.0


def refusing(journal: Journal, key: StageKey) -> Callable[[AppConfig, Secrets], None]:
    def preflight(config: AppConfig, secrets: Secrets) -> None:
        journal.add(f"preflight:{key}")
        raise StageFailedError(f"{key} cannot run")

    return preflight


def test_a_failed_preflight_stops_the_run_before_any_work(
    run: Runner, journal: Journal, recording_sink: RecordingSink
):
    asr = fake_stage(StageKey.ASR, journal)
    pipeline = Pipeline(
        (
            fake_stage(StageKey.METADATA, journal),
            replace(asr, preflight=refusing(journal, StageKey.ASR)),
        )
    )

    with pytest.raises(StageFailedError, match="asr cannot run"):
        run(pipeline)

    assert journal.entries == ["preflight:asr"]
    assert recording_sink.events == []


def test_preflight_covers_only_the_stages_the_run_executes(
    run: Runner, journal: Journal, state: ProjectState
):
    state.mark_done(StageKey.METADATA, elapsed_s=1.0)
    stages = (
        fake_stage(StageKey.METADATA, journal),
        fake_stage(StageKey.DOWNLOAD, journal),
        fake_stage(StageKey.CHAT_FETCH, journal, enabled=False),
        fake_stage(StageKey.ASR, journal),
    )
    pipeline = Pipeline(
        tuple(
            stage
            if stage.key is StageKey.DOWNLOAD
            else replace(stage, preflight=refusing(journal, stage.key))
            for stage in stages
        )
    )

    run(pipeline, break_after=StageKey.DOWNLOAD)

    assert journal.entries == ["on_skip:metadata", "run:download@download"]


def test_disabled_stage_is_not_recorded(
    run: Runner, journal: Journal, state: ProjectState
):
    run(Pipeline((fake_stage(StageKey.CHAT_FETCH, journal, enabled=False),)))
    assert journal.entries == []
    assert not state.is_done(StageKey.CHAT_FETCH)


def test_break_after_stops_and_skips_side_tasks_delivery_and_archive(
    run: Runner,
    journal: Journal,
    recording_sink: RecordingSink,
    layout: ProjectLayout,
    archive: Archive,
):
    pipeline = Pipeline(
        (
            fake_stage(StageKey.METADATA, journal),
            fake_stage(StageKey.DOWNLOAD, journal),
            fake_stage(StageKey.COMBINE, journal),
        ),
        side_tasks=(fake_side_task(SideTaskKey.COVER, StageKey.METADATA, journal),),
        delivery=(fake_delivery("package", journal),),
    )
    assert run(pipeline, archive=archive, break_after=StageKey.DOWNLOAD) == layout
    assert journal.entries == [
        "run:metadata@metadata",
        "side_skip:cover:breakpoint",
        "run:download@download",
    ]
    events = recording_sink.events
    started = events[0]
    assert isinstance(started, RunStarted)
    assert [entry.enabled for entry in started.plan] == [
        True,
        True,
        True,
        False,
        False,
        False,
    ]
    assert steps(events)[-4:] == [
        StepCompleted("download", PlanKind.STAGE, 1.0),
        StepSkipped("combine", PlanKind.STAGE, SkipReason.BREAKPOINT),
        StepSkipped("package", PlanKind.DELIVERY, SkipReason.BREAKPOINT),
        StepSkipped("archive", PlanKind.DELIVERY, SkipReason.BREAKPOINT),
    ]
    assert StepSkipped("cover", PlanKind.SIDE_TASK, SkipReason.BREAKPOINT) in events
    assert events[-1] == RunFinished(RunOutcome.COMPLETED)
    assert layout.root.is_dir()


def test_break_after_a_disabled_stage_still_stops(run: Runner, journal: Journal):
    pipeline = Pipeline(
        (
            fake_stage(StageKey.CHAT_FETCH, journal, enabled=False),
            fake_stage(StageKey.AUDIO, journal),
        )
    )
    run(pipeline, break_after=StageKey.CHAT_FETCH)
    assert journal.entries == []


def test_break_after_reports_every_later_stage_skipped(
    run: Runner, journal: Journal, recording_sink: RecordingSink
):
    pipeline = Pipeline(
        (
            fake_stage(StageKey.METADATA, journal),
            fake_stage(StageKey.CHAT_FETCH, journal, enabled=False),
            fake_stage(StageKey.AUDIO, journal),
        )
    )
    run(pipeline, break_after=StageKey.METADATA)
    assert steps(recording_sink.events)[-2:] == [
        StepSkipped("chat_fetch", PlanKind.STAGE, SkipReason.DISABLED),
        StepSkipped("audio", PlanKind.STAGE, SkipReason.BREAKPOINT),
    ]


def test_break_after_reports_later_completed_stages_as_complete(
    run: Runner, journal: Journal, recording_sink: RecordingSink, state: ProjectState
):
    state.mark_done(StageKey.AUDIO, elapsed_s=1.0)
    pipeline = Pipeline(
        (
            fake_stage(StageKey.METADATA, journal),
            fake_stage(StageKey.AUDIO, journal),
            fake_stage(StageKey.ASR, journal),
        )
    )
    run(pipeline, break_after=StageKey.METADATA)
    assert journal.entries == ["run:metadata@metadata"]
    assert steps(recording_sink.events)[-2:] == [
        StepSkipped("audio", PlanKind.STAGE, SkipReason.ALREADY_COMPLETE),
        StepSkipped("asr", PlanKind.STAGE, SkipReason.BREAKPOINT),
    ]


def test_break_after_an_unregistered_stage_is_refused(run: Runner, journal: Journal):
    with pytest.raises(ValueError, match="not registered"):
        run(
            Pipeline((fake_stage(StageKey.METADATA, journal),)),
            break_after=StageKey.ASR,
        )
    assert journal.entries == []


def test_stage_failure_is_reported_and_raised(
    run: Runner,
    journal: Journal,
    recording_sink: RecordingSink,
    state: ProjectState,
    layout: ProjectLayout,
):
    pipeline = Pipeline(
        (
            fake_stage(StageKey.METADATA, journal, action=failing("no network")),
            fake_stage(StageKey.DOWNLOAD, journal),
        ),
        delivery=(fake_delivery("package", journal),),
    )
    with pytest.raises(StageFailedError, match="no network"):
        run(pipeline)
    assert journal.entries == ["run:metadata@metadata"]
    assert recording_sink.events[-2:] == [
        StepFailed("metadata", PlanKind.STAGE, "no network"),
        RunFinished(RunOutcome.FAILED, "no network"),
    ]
    assert not state.is_done(StageKey.METADATA)
    text = layout.run_log(STARTED_AT).read_text(encoding="utf-8")
    assert "[metadata] stage metadata failed: no network" in text
    assert "StageFailedError" in text  # the traceback, at DEBUG


def test_side_task_is_joined_when_a_stage_fails(
    run: Runner, journal: Journal, recording_sink: RecordingSink
):
    release = threading.Event()

    def slow_cover(ctx: StageContext) -> str:
        release.wait(timeout=5)
        time.sleep(0.05)
        journal.add("cover finished")
        return "cover.png"

    def fail_after_release(ctx: StageContext) -> None:
        release.set()
        raise StageFailedError("download broke")

    pipeline = Pipeline(
        (
            fake_stage(StageKey.METADATA, journal),
            fake_stage(StageKey.DOWNLOAD, journal, action=fail_after_release),
        ),
        side_tasks=(
            fake_side_task(
                SideTaskKey.COVER, StageKey.METADATA, journal, action=slow_cover
            ),
        ),
    )
    with pytest.raises(StageFailedError):
        run(pipeline)
    assert "cover finished" in journal.entries
    events = recording_sink.events
    completed = next(
        index
        for index, event in enumerate(events)
        if isinstance(event, StepCompleted) and event.key == "cover"
    )
    assert events[completed] == replace(
        events[completed], kind=PlanKind.SIDE_TASK, result="cover.png"
    )
    assert completed < events.index(RunFinished(RunOutcome.FAILED, "download broke"))


def test_side_task_runs_in_its_own_scope_thread_and_workdir(
    run: Runner, journal: Journal, layout: ProjectLayout, bus: EventBus
):
    seen: list[object] = []
    started: list[tuple[str | None, bool]] = []

    class StartWatcher:
        def emit(self, event: Event) -> None:
            if isinstance(event, StepStarted) and event.key == "cover":
                on_main = threading.current_thread() is threading.main_thread()
                started.append((current_stage(), on_main))

    def cover(ctx: StageContext) -> None:
        seen.extend([current_stage(), ctx.workdir])

    bus.subscribe(StartWatcher())
    run(cover_pipeline(journal, action=cover))
    assert seen == ["cover", layout.side_dir(SideTaskKey.COVER)]
    assert layout.side_dir(SideTaskKey.COVER).is_dir()
    assert started == [("cover", False)]


def test_side_task_starts_after_an_already_complete_stage(
    run: Runner, journal: Journal, state: ProjectState
):
    state.mark_done(StageKey.METADATA, elapsed_s=1.0)
    run(cover_pipeline(journal))
    assert journal.entries == ["on_skip:metadata", "side:cover@cover"]


@pytest.mark.parametrize(
    ("enabled", "done", "reason"),
    [
        pytest.param(False, False, SkipReason.DISABLED, id="disabled"),
        pytest.param(True, True, SkipReason.ALREADY_COMPLETE, id="done"),
    ],
)
def test_side_task_skips(
    run: Runner,
    journal: Journal,
    recording_sink: RecordingSink,
    state: ProjectState,
    *,
    enabled: bool,
    done: bool,
    reason: SkipReason,
):
    if done:
        state.side_tasks.cover = TaskRecord(completed_at=now(), elapsed_s=1.0)
    run(cover_pipeline(journal, enabled=enabled))
    assert journal.entries == ["run:metadata@metadata", f"side_skip:cover:{reason}"]
    assert StepSkipped("cover", PlanKind.SIDE_TASK, reason) in recording_sink.events


def test_side_task_failure_does_not_fail_the_run(
    run: Runner, journal: Journal, recording_sink: RecordingSink, layout: ProjectLayout
):
    def broken(ctx: StageContext) -> None:
        raise RuntimeError("image backend down")

    run(cover_pipeline(journal, action=broken))
    assert StepFailed("cover", PlanKind.SIDE_TASK, "image backend down") in (
        recording_sink.events
    )
    assert recording_sink.events[-1] == RunFinished(RunOutcome.COMPLETED)
    text = layout.run_log(STARTED_AT).read_text(encoding="utf-8")
    assert "WARNING  | [cover] side_task cover failed: image backend down" in text


def test_workdir_is_created_only_when_used(
    run: Runner, journal: Journal, layout: ProjectLayout
):
    def touch(ctx: StageContext) -> None:
        (ctx.workdir / "info.json").write_text("{}", encoding="utf-8")

    pipeline = Pipeline(
        (
            fake_stage(StageKey.METADATA, journal, action=touch),
            fake_stage(StageKey.TRANSCRIPT, journal),
        )
    )
    run(pipeline)
    assert (layout.work_dir(StageKey.METADATA) / "info.json").is_file()
    assert not layout.work_dir(StageKey.TRANSCRIPT).exists()


def test_stage_state_changes_are_saved(
    run: Runner, journal: Journal, layout: ProjectLayout
):
    def rename(ctx: StageContext) -> None:
        ctx.state.name = "全力脱力タイムズ"
        ctx.save()

    run(Pipeline((fake_stage(StageKey.METADATA, journal, action=rename),)))
    assert load_state(layout).name == "全力脱力タイムズ"


def test_agent_usage_is_summed_into_the_ledger(
    run: Runner, journal: Journal, state: ProjectState
):
    def two_sessions(ctx: StageContext) -> None:
        for tokens in (10, 5):
            ctx.events.emit(
                AgentSessionFinished(
                    "prepass", SessionOutcome.OK, 1.0, 0, {"input_tokens": tokens}
                )
            )

    run(Pipeline((fake_stage(StageKey.PREPASS, journal, action=two_sessions),)))
    assert state.stages[StageKey.PREPASS].agent_usage == {"input_tokens": 15}


def test_writes_run_log_and_events_log(
    run: Runner, journal: Journal, layout: ProjectLayout, bus: EventBus
):
    def chatty(ctx: StageContext) -> None:
        logger.debug("fetching info")

    run(Pipeline((fake_stage(StageKey.METADATA, journal, action=chatty),)))
    text = layout.run_log(STARTED_AT).read_text(encoding="utf-8")
    assert "[metadata] fetching info" in text
    lines = layout.events_log(STARTED_AT).read_text(encoding="utf-8").splitlines()
    types = [json.loads(line)["type"] for line in lines]
    assert types[0] == "RunStarted"
    assert types[-1] == "RunFinished"
    # The JSONL sink is gone after the run: this event reaches no file.
    bus.emit(RunFinished(RunOutcome.COMPLETED))
    assert len(
        layout.events_log(STARTED_AT).read_text(encoding="utf-8").splitlines()
    ) == len(lines)


def test_delivery_skips_disabled_steps_and_works_in_its_declared_dir(
    run: Runner, journal: Journal, recording_sink: RecordingSink, layout: ProjectLayout
):
    workdirs: list[Path] = []

    def package(ctx: StageContext) -> str:
        workdirs.append(ctx.workdir)
        return "video.mp4"

    pipeline = Pipeline(
        (fake_stage(StageKey.METADATA, journal),),
        delivery=(
            fake_delivery("upload", journal, enabled=False),
            fake_delivery("package", journal, action=package),
        ),
    )
    run(pipeline)
    assert journal.entries == ["run:metadata@metadata", "deliver:package@package"]
    assert workdirs == [layout.work_root / "package"]
    events = recording_sink.events
    assert StepSkipped("upload", PlanKind.DELIVERY, SkipReason.DISABLED) in events
    assert StepCompleted("package", PlanKind.DELIVERY, 1.0, "video.mp4") in events


def test_archive_runs_last_with_the_logs_closed(
    run: Runner,
    journal: Journal,
    layout: ProjectLayout,
    archive: Archive,
    archived: ProjectLayout,
):
    def work(ctx: StageContext) -> None:
        logger.info("working")

    def package(ctx: StageContext) -> None:
        assert ctx.layout == layout  # package reads the local project
        logger.info("packaging")

    pipeline = Pipeline(
        (fake_stage(StageKey.METADATA, journal, action=work),),
        delivery=(fake_delivery("package", journal, action=package),),
    )
    assert run(pipeline, archive=archive) == archived
    assert not layout.root.exists()
    text = archived.run_log(STARTED_AT).read_text(encoding="utf-8")
    assert "[metadata] working" in text
    assert "[package] packaging" in text
    assert "moving" not in text
    lines = archived.events_log(STARTED_AT).read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["type"] == "StepCompleted"  # package's
    assert load_state(archived).is_done(StageKey.METADATA)


def test_archive_failure_fails_the_run(
    run: Runner, journal: Journal, recording_sink: RecordingSink
):
    def broken(layout: ProjectLayout) -> ProjectLayout:
        raise OSError("NAS unreachable")

    with pytest.raises(OSError, match="NAS unreachable"):
        run(Pipeline((fake_stage(StageKey.METADATA, journal),)), archive=broken)
    assert recording_sink.events[-2:] == [
        StepFailed("archive", PlanKind.DELIVERY, "NAS unreachable"),
        RunFinished(RunOutcome.FAILED, "NAS unreachable"),
    ]


def test_run_project_creates_and_runs_the_project(
    loaded: LoadedConfig, journal: Journal, recording_sink: RecordingSink
):
    final = run_project(
        loaded,
        RunOptions(source=SourceId(Platform.TVER, "epnew1")),
        sinks=[recording_sink],
        pipeline=Pipeline((fake_stage(StageKey.METADATA, journal),)),
    )
    assert final == ProjectLayout.for_id(loaded.projects_root, "epnew1")
    assert load_state(final).is_done(StageKey.METADATA)
    assert recording_sink.events[-1] == RunFinished(RunOutcome.COMPLETED)


def test_run_project_checks_options_before_creating_anything(
    loaded: LoadedConfig, journal: Journal, recording_sink: RecordingSink
):
    options = RunOptions(
        source=SourceId(Platform.TVER, "epnew1"), break_after=StageKey.ASR
    )
    with pytest.raises(ValueError, match="not registered"):
        run_project(
            loaded,
            options,
            sinks=[recording_sink],
            pipeline=Pipeline((fake_stage(StageKey.METADATA, journal),)),
        )
    assert not loaded.projects_root.exists()
    assert recording_sink.events == []


def test_run_project_preflights_before_creating_anything(
    loaded: LoadedConfig, journal: Journal
):
    asr = fake_stage(StageKey.ASR, journal)
    with pytest.raises(StageFailedError):
        run_project(
            loaded,
            RunOptions(source=SourceId(Platform.TVER, "epnew1")),
            sinks=[],
            pipeline=Pipeline(
                (replace(asr, preflight=refusing(journal, StageKey.ASR)),)
            ),
        )
    assert not loaded.projects_root.exists()


def test_run_project_skips_the_preflight_of_completed_stages(
    loaded: LoadedConfig, journal: Journal, state: ProjectState, layout: ProjectLayout
):
    state.mark_done(StageKey.ASR, elapsed_s=1.0)
    save_state(layout, state)
    asr = fake_stage(StageKey.ASR, journal)

    run_project(
        loaded,
        RunOptions(source=state.source_id),
        sinks=[],
        pipeline=Pipeline((replace(asr, preflight=refusing(journal, StageKey.ASR)),)),
    )

    assert journal.entries == ["on_skip:asr"]
