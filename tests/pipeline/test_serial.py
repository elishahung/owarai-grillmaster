"""`SerialRun`: the `grill serial` chain over `run_project`."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger
from tests.pipeline.fakes import (
    Journal,
    StageFailedError,
    failing,
    fake_delivery,
    fake_stage,
)

from grillmaster.config.load import LoadedConfig
from grillmaster.config.model import validate_config
from grillmaster.core.source_id import Platform, SourceId
from grillmaster.core.stage_key import StageKey
from grillmaster.events.types import BatchItemStarted, RunStarted
from grillmaster.pipeline.registry import Pipeline
from grillmaster.pipeline.runner import ArchivedDeliveryError
from grillmaster.pipeline.serial import SerialRun
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.store import load_state, save_state
from grillmaster.stages.base import RunOptions

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.fakes import RecordingSink

    from grillmaster.project.state import ProjectState
    from grillmaster.stages.base import StageContext

EP1 = SourceId(Platform.TVER, "epone111")
EP2 = SourceId(Platform.TVER, "eptwo222")
EP3 = SourceId(Platform.TVER, "epthree3")


@pytest.fixture
def journal() -> Journal:
    return Journal()


@pytest.fixture
def errors() -> Iterator[list[str]]:
    messages: list[str] = []
    handler = logger.add(
        lambda message: messages.append(message.record["message"]), level="ERROR"
    )
    yield messages
    logger.remove(handler)


def metadata_only(journal: Journal, fail_on: set[str] | None = None) -> Pipeline:
    """One stage that fails for the project IDs in `fail_on` (while they
    stay in the set)."""

    def action(ctx: StageContext) -> None:
        if fail_on and ctx.state.id in fail_on:
            raise StageFailedError(f"boom {ctx.state.id}")

    return Pipeline((fake_stage(StageKey.METADATA, journal, action=action),))


def serial(*sources: SourceId, **options: object) -> SerialRun:
    return SerialRun(sources, RunOptions(source=sources[0], **options))  # pyright: ignore[reportArgumentType]


def test_each_project_seeds_the_next(
    loaded: LoadedConfig,
    journal: Journal,
    recording_sink: RecordingSink,
    state: ProjectState,
    tmp_path: Path,
):
    parent = tmp_path / "earlier"
    save_state(ProjectLayout(parent), state)
    final = serial(EP1, EP2, EP3, parent=parent, cover=True).run(
        loaded, sinks=[recording_sink], pipeline=metadata_only(journal)
    )

    layouts = [
        ProjectLayout.for_id(loaded.projects_root, s.video_id) for s in (EP1, EP2, EP3)
    ]
    assert final == layouts[-1]
    parents = [load_state(layout).parent for layout in layouts]
    assert parents == [parent.resolve(), layouts[0].root, layouts[1].root]
    batch = [e for e in recording_sink.events if isinstance(e, BatchItemStarted)]
    assert batch == [
        BatchItemStarted(1, 3, "epone111"),
        BatchItemStarted(2, 3, "eptwo222"),
        BatchItemStarted(3, 3, "epthree3"),
    ]
    # Each item is announced before its own run starts.
    kinds = [
        type(e).__name__
        for e in recording_sink.events
        if isinstance(e, BatchItemStarted | RunStarted)
    ]
    assert kinds == ["BatchItemStarted", "RunStarted"] * 3


def test_failure_stops_the_chain_and_a_rerun_continues_it(
    loaded: LoadedConfig,
    journal: Journal,
    recording_sink: RecordingSink,
    errors: list[str],
):
    fail_on = {EP2.video_id}
    pipeline = metadata_only(journal, fail_on)
    chain = serial(EP1, EP2, EP3)

    with pytest.raises(StageFailedError, match="boom eptwo222"):
        chain.run(loaded, sinks=[recording_sink], pipeline=pipeline)

    first = ProjectLayout.for_id(loaded.projects_root, EP1.video_id)
    assert chain.position == 1
    assert not ProjectLayout.for_id(loaded.projects_root, EP3.video_id).root.exists()
    assert f"grill serial eptwo222 epthree3 --parent {first.root}" in errors[-1]

    fail_on.clear()
    final = chain.run(loaded, sinks=[recording_sink], pipeline=pipeline)

    assert final == ProjectLayout.for_id(loaded.projects_root, EP3.video_id)
    assert final is not None
    assert journal.entries == [
        "run:metadata@metadata",
        "run:metadata@metadata",  # the failed attempt at EP2
        "run:metadata@metadata",
        "run:metadata@metadata",
    ]
    assert (
        load_state(final).parent
        == ProjectLayout.for_id(loaded.projects_root, EP2.video_id).root
    )


def test_a_package_failure_after_the_archive_moves_the_chain_past_it(
    *,
    loaded: LoadedConfig,
    roles: dict[str, str],
    journal: Journal,
    recording_sink: RecordingSink,
    errors: list[str],
    tmp_path: Path,
):
    archive_root = tmp_path / "archive"
    config = validate_config(
        {"agents": {"roles": roles}, "paths": {"archive": str(archive_root)}},
        root=loaded.root,
    )
    archiving = LoadedConfig(root=loaded.root, config=config, secrets=loaded.secrets)
    pipeline = Pipeline(
        (fake_stage(StageKey.METADATA, journal),),
        delivery=(fake_delivery("package", journal, action=failing("render broke")),),
    )
    chain = serial(EP1, EP2)

    with pytest.raises(ArchivedDeliveryError):
        chain.run(archiving, sinks=[recording_sink], pipeline=pipeline)

    # Re-running EP1's source would start it over locally: the chain moves on.
    archived = archive_root / "etc" / EP1.video_id
    assert (chain.position, chain.parent) == (1, archived)
    assert f"grill serial eptwo222 --parent {archived}" in errors[-1]


def test_resume_command_quotes_paths_with_spaces():
    chain = SerialRun(
        (EP1, EP2), RunOptions(source=EP1, parent=Path("C:/my projects/ep0"))
    )
    assert chain.resume_command() == (
        f'grill serial epone111 eptwo222 --parent "{Path("C:/my projects/ep0")}"'
    )


@pytest.mark.parametrize(
    ("sources", "options", "message"),
    [
        pytest.param((), {}, "at least one", id="empty"),
        pytest.param((EP1, EP2, EP1), {}, "Duplicate sources: epone111", id="dup"),
        pytest.param(
            (EP1,), {"break_after": StageKey.ASR}, "--break-after", id="break"
        ),
    ],
)
def test_bad_chains_are_refused(
    sources: tuple[SourceId, ...], options: dict[str, object], message: str
):
    with pytest.raises(ValueError, match=message):
        SerialRun(sources, RunOptions(source=EP1, **options))  # pyright: ignore[reportArgumentType]
