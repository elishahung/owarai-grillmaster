"""`run_project`'s archive wiring and `deliver_project` (the `grill package` run)."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, cast

import pytest
from tests.pipeline.fakes import Journal, fake_delivery, fake_stage

from grillmaster.agents.workspace import BOUNDARY_NAME
from grillmaster.config.load import LoadedConfig
from grillmaster.config.model import validate_config
from grillmaster.core.source_id import Platform, SourceId
from grillmaster.core.stage_key import StageKey
from grillmaster.events.types import (
    PlanKind,
    RunFinished,
    RunOutcome,
    RunStarted,
)
from grillmaster.package.errors import PackageError
from grillmaster.pipeline.registry import Pipeline
from grillmaster.pipeline.runner import (
    ArchivedDeliveryError,
    ProjectRun,
    archive_to,
    deliver_project,
    run_project,
)
from grillmaster.project.errors import ProjectBusyError, ProjectExistsError
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.store import load_state, project_lock, save_state
from grillmaster.stages.base import RunOptions

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from tests.fakes import RecordingSink

    from grillmaster.events.types import Event
    from grillmaster.project.state import ProjectState
    from grillmaster.stages.base import StageContext

SOURCE = SourceId(Platform.TVER, "epnew1")


@pytest.fixture
def journal() -> Journal:
    return Journal()


@pytest.fixture
def archive_root(tmp_path: Path) -> Path:
    return tmp_path / "archive"


@pytest.fixture
def archiving(loaded: LoadedConfig, archive_root: Path) -> LoadedConfig:
    """`loaded` with `[paths] archive` set."""
    data = {
        "agents": {"roles": loaded.config.agents.roles.model_dump()},
        "paths": {"archive": str(archive_root)},
    }
    config = validate_config(data, root=loaded.root)
    return LoadedConfig(root=loaded.root, config=config, secrets=loaded.secrets)


def plan_keys(events: Sequence[Event]) -> list[str]:
    started = next(event for event in events if isinstance(event, RunStarted))
    return [entry.key for entry in started.plan]


def test_run_project_archives_into_the_configured_root(
    archiving: LoadedConfig,
    archive_root: Path,
    journal: Journal,
    recording_sink: RecordingSink,
):
    final = run_project(
        archiving,
        RunOptions(source=SOURCE),
        sinks=[recording_sink],
        pipeline=Pipeline((fake_stage(StageKey.METADATA, journal),)),
    )

    # Undated and unnamed: `etc/<id>`.
    assert final == ProjectLayout(archive_root / "etc" / "epnew1")
    assert load_state(final).is_done(StageKey.METADATA)
    assert not ProjectLayout.for_id(archiving.projects_root, "epnew1").root.exists()
    assert plan_keys(recording_sink.events)[-1] == "archive"
    assert recording_sink.events[-1] == RunFinished(RunOutcome.COMPLETED)


def test_archive_destination_uses_what_the_run_recorded(
    archiving: LoadedConfig,
    archive_root: Path,
    journal: Journal,
    recording_sink: RecordingSink,
):
    def name_it(ctx: StageContext) -> None:
        ctx.state.name = "show"
        ctx.state.broadcast_date = date(2025, 10, 9)
        ctx.save()

    final = run_project(
        archiving,
        RunOptions(source=SOURCE),
        sinks=[recording_sink],
        pipeline=Pipeline((fake_stage(StageKey.METADATA, journal, action=name_it),)),
    )

    assert final == ProjectLayout(archive_root / "25" / "10" / "251009_epnew1_show")


def test_run_project_without_an_archive_root_stays_in_place(
    loaded: LoadedConfig, journal: Journal, recording_sink: RecordingSink
):
    final = run_project(
        loaded,
        RunOptions(source=SOURCE),
        sinks=[recording_sink],
        pipeline=Pipeline((fake_stage(StageKey.METADATA, journal),)),
    )

    assert final == ProjectLayout.for_id(loaded.projects_root, "epnew1")
    assert "archive" not in plan_keys(recording_sink.events)


def test_a_run_seals_the_projects_root_for_agent_clis(
    loaded: LoadedConfig, journal: Journal
):
    run_project(
        loaded,
        RunOptions(source=SOURCE),
        sinks=[],
        pipeline=Pipeline((fake_stage(StageKey.METADATA, journal),)),
    )
    assert (loaded.projects_root / BOUNDARY_NAME).is_file()


def test_archive_to_moves_with_the_saved_state(
    tmp_path: Path, archive_root: Path, state: ProjectState
):
    layout = ProjectLayout(tmp_path / "projects" / state.id)
    state.name = "show"
    save_state(layout, state)

    moved = archive_to(archive_root)(layout)

    assert moved == ProjectLayout(archive_root / "etc" / f"{state.id}_show")
    assert load_state(moved).id == state.id


def test_deliver_project_runs_only_the_delivery_steps(
    archiving: LoadedConfig,
    journal: Journal,
    recording_sink: RecordingSink,
    tmp_path: Path,
    state: ProjectState,
):
    # An archived project, outside `projects/`.
    layout = ProjectLayout(tmp_path / "archive" / "25" / "10" / state.id)
    save_state(layout, state)
    seen: list[ProjectLayout] = []

    def record(ctx: StageContext) -> str:
        seen.append(ctx.layout)
        return "packaged"

    pipeline = Pipeline(
        (fake_stage(StageKey.METADATA, journal),),
        delivery=(fake_delivery("package", journal, action=record),),
    )

    final = deliver_project(
        archiving,
        layout,
        RunOptions(source=state.source_id, remix="noise"),
        sinks=[recording_sink],
        pipeline=pipeline,
    )

    assert final == layout
    assert journal.entries == ["deliver:package@package"]
    assert seen == [layout]
    started = next(e for e in recording_sink.events if isinstance(e, RunStarted))
    assert [(entry.key, entry.kind) for entry in started.plan] == [
        ("package", PlanKind.DELIVERY)
    ]
    assert list(layout.logs_dir.glob("run-*.log"))


def test_package_runs_on_the_archived_project(
    archiving: LoadedConfig,
    archive_root: Path,
    journal: Journal,
    recording_sink: RecordingSink,
):
    roots: list[Path] = []

    def package(ctx: StageContext) -> None:
        roots.append(ctx.layout.root)

    final = run_project(
        archiving,
        RunOptions(source=SOURCE),
        sinks=[recording_sink],
        pipeline=Pipeline(
            (fake_stage(StageKey.METADATA, journal),),
            delivery=(fake_delivery("package", journal, action=package),),
        ),
    )

    assert roots == [archive_root / "etc" / "epnew1"] == [final.root]
    assert plan_keys(recording_sink.events) == ["metadata", "archive", "package"]


def test_a_failed_package_after_the_archive_gives_the_resume_command(
    archiving: LoadedConfig,
    archive_root: Path,
    journal: Journal,
    recording_sink: RecordingSink,
):
    def render_fails(ctx: StageContext) -> str:
        raise PackageError("burn-in output duration differs")

    pipeline = Pipeline(
        (fake_stage(StageKey.METADATA, journal),),
        delivery=(fake_delivery("package", journal, action=render_fails),),
    )

    with pytest.raises(ArchivedDeliveryError) as caught:
        run_project(
            archiving,
            RunOptions(source=SOURCE),
            sinks=[recording_sink],
            pipeline=pipeline,
        )

    archived = ProjectLayout(archive_root / "etc" / "epnew1")
    assert isinstance(caught.value.__cause__, PackageError)
    assert str(caught.value) == (
        "burn-in output duration differs (the project is already archived; "
        f'resume with: grill package "{archived.root}")'
    )
    assert load_state(archived).is_done(StageKey.METADATA)
    assert not ProjectLayout.for_id(archiving.projects_root, "epnew1").root.exists()
    assert recording_sink.events[-1] == RunFinished(
        RunOutcome.FAILED, str(caught.value)
    )


def test_rerunning_an_archived_source_is_refused_before_any_preflight(
    archiving: LoadedConfig, journal: Journal, recording_sink: RecordingSink
):
    checked: list[ProjectState | None] = []

    def preflight(options: RunOptions, config: object, state: object) -> None:
        checked.append(cast("ProjectState | None", state))

    pipeline = Pipeline(
        (fake_stage(StageKey.METADATA, journal),),
        delivery=(fake_delivery("package", journal, preflight=preflight),),
    )
    final = run_project(
        archiving, RunOptions(source=SOURCE), sinks=[], pipeline=pipeline
    )
    checks = len(checked)

    with pytest.raises(ProjectExistsError, match="stages cannot re-run") as caught:
        run_project(
            archiving,
            RunOptions(source=SOURCE),
            sinks=[recording_sink],
            pipeline=pipeline,
        )

    assert f'grill package "{final.root}"' in str(caught.value)
    assert not ProjectLayout.for_id(archiving.projects_root, "epnew1").root.exists()
    assert journal.entries == ["run:metadata@metadata", "deliver:package@package"]
    assert recording_sink.events == []
    assert len(checked) == checks  # refused before any preflight


def test_project_run_retries_only_the_delivery_after_the_archive(
    archiving: LoadedConfig,
    archive_root: Path,
    journal: Journal,
    recording_sink: RecordingSink,
):
    attempts = iter([PackageError("NVENC busy"), PackageError("still busy"), None])

    def package(ctx: StageContext) -> None:
        if (error := next(attempts)) is not None:
            raise error

    run = ProjectRun(
        archiving,
        RunOptions(source=SOURCE),
        Pipeline(
            (fake_stage(StageKey.METADATA, journal),),
            delivery=(fake_delivery("package", journal, action=package),),
        ),
    )
    archived = ProjectLayout(archive_root / "etc" / "epnew1")

    with pytest.raises(ArchivedDeliveryError, match="NVENC busy"):
        run.run([recording_sink])
    # A failed retry still names the archived directory.
    with pytest.raises(ArchivedDeliveryError, match="still busy") as caught:
        run.run([recording_sink])
    assert caught.value.archived == archived
    assert run.run([recording_sink]) == archived

    assert journal.entries == [
        "run:metadata@metadata",
        "deliver:package@package",
        "deliver:package@package",
        "deliver:package@package",
    ]
    assert not ProjectLayout.for_id(archiving.projects_root, "epnew1").root.exists()


def test_a_failing_delivery_preflight_creates_nothing(
    loaded: LoadedConfig, journal: Journal
):
    def missing_pool(options: RunOptions, config: object, state: object) -> None:
        raise PackageError("pool sleep is missing")

    pipeline = Pipeline(
        (fake_stage(StageKey.METADATA, journal),),
        delivery=(fake_delivery("package", journal, preflight=missing_pool),),
    )
    with pytest.raises(PackageError, match="sleep"):
        run_project(loaded, RunOptions(source=SOURCE), sinks=[], pipeline=pipeline)
    assert not ProjectLayout.for_id(loaded.projects_root, SOURCE.video_id).root.exists()
    assert journal.entries == []


# --- the per-project run lock -------------------------------------------------------


def lock_probe(loaded: LoadedConfig, seen: list[str]) -> Callable[[StageContext], None]:
    """A step body recording whether another holder could take the run lock."""

    def probe(ctx: StageContext) -> None:
        try:
            with project_lock(loaded.projects_root, SOURCE.video_id):
                seen.append("free")
        except ProjectBusyError:
            seen.append("held")

    return probe


def test_the_run_holds_its_project_through_the_archive_and_package(
    archiving: LoadedConfig, journal: Journal
):
    seen: list[str] = []
    probe = lock_probe(archiving, seen)
    run_project(
        archiving,
        RunOptions(source=SOURCE),
        sinks=[],
        pipeline=Pipeline(
            (fake_stage(StageKey.METADATA, journal, action=probe),),
            delivery=(fake_delivery("package", journal, action=probe),),
        ),
    )
    assert seen == ["held", "held"]
    with project_lock(archiving.projects_root, SOURCE.video_id):  # released
        pass


def test_a_project_another_process_holds_is_refused_before_anything_runs(
    loaded: LoadedConfig, journal: Journal, recording_sink: RecordingSink
):
    pipeline = Pipeline((fake_stage(StageKey.METADATA, journal),))
    with (
        project_lock(loaded.projects_root, SOURCE.video_id),
        pytest.raises(ProjectBusyError, match="epnew1 is in use"),
    ):
        run_project(
            loaded, RunOptions(source=SOURCE), sinks=[recording_sink], pipeline=pipeline
        )
    assert journal.entries == []
    assert recording_sink.events == []
    assert not ProjectLayout.for_id(loaded.projects_root, SOURCE.video_id).root.exists()


def test_packaging_an_archived_project_takes_its_lock(
    archiving: LoadedConfig, journal: Journal, tmp_path: Path, state: ProjectState
):
    layout = ProjectLayout(tmp_path / "archive" / "25" / "10" / state.id)
    save_state(layout, state)
    pipeline = Pipeline((), delivery=(fake_delivery("package", journal),))
    options = RunOptions(source=state.source_id)

    with (
        project_lock(archiving.projects_root, state.id),
        pytest.raises(ProjectBusyError),
    ):
        deliver_project(archiving, layout, options, sinks=[], pipeline=pipeline)
    assert journal.entries == []

    deliver_project(archiving, layout, options, sinks=[], pipeline=pipeline)
    assert journal.entries == ["deliver:package@package"]
