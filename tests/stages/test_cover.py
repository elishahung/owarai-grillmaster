from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.fakes import FakeAgentRunner, draws
from tests.stages.conftest import complete_side_task

from grillmaster.agents.errors import AgentQuotaError
from grillmaster.core.stage_key import SideTaskKey, StageKey
from grillmaster.extras.cover import POSTER_NAME, TASK_NAME
from grillmaster.extras.errors import ExtrasError
from grillmaster.project.store import load_state
from grillmaster.stages.cover import TASK

if TYPE_CHECKING:
    from pathlib import Path

    from tests.stages.conftest import MakeContext

    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState


@pytest.fixture
def script() -> dict[str, object]:
    return {TASK_NAME: draws}


@pytest.fixture
def poster(layout: ProjectLayout) -> Path:
    layout.poster.write_bytes(b"jpeg")
    return layout.poster


def test_starts_after_the_download_that_fetches_the_poster() -> None:
    assert TASK.key is SideTaskKey.COVER
    assert TASK.start_after is StageKey.DOWNLOAD


@pytest.mark.usefixtures("poster")
def test_draws_the_root_cover_in_its_side_directory(
    make_context: MakeContext,
    layout: ProjectLayout,
    state: ProjectState,
    agents: FakeAgentRunner,
) -> None:
    summary = complete_side_task(TASK, make_context(SideTaskKey.COVER))

    assert summary == "cover.png"
    assert layout.cover.read_bytes() == b"png"
    side = layout.side_dir(SideTaskKey.COVER)
    task = agents.task(TASK_NAME)
    assert task.workdir == side
    assert task.session_dir == side / "session"
    assert task.images == (side / POSTER_NAME,)
    record = load_state(layout).side_tasks.cover
    assert record is not None
    assert record.elapsed_s == 1.0
    assert TASK.done(state)


@pytest.mark.usefixtures("poster")
def test_an_existing_cover_is_recorded_without_the_agent(
    make_context: MakeContext,
    layout: ProjectLayout,
    agents: FakeAgentRunner,
) -> None:
    layout.cover.write_bytes(b"kept")

    complete_side_task(TASK, make_context(SideTaskKey.COVER))

    assert agents.tasks == []
    assert load_state(layout).side_tasks.cover is not None


def test_missing_poster_fails_unrecorded(
    make_context: MakeContext, layout: ProjectLayout
) -> None:
    with pytest.raises(ExtrasError):
        TASK.run(make_context(SideTaskKey.COVER))

    assert load_state(layout).side_tasks.cover is None


@pytest.mark.usefixtures("poster")
def test_agent_failure_leaves_the_task_unrecorded(
    make_context: MakeContext,
    layout: ProjectLayout,
    agents: FakeAgentRunner,
) -> None:
    agents.script[TASK_NAME] = AgentQuotaError("429")

    with pytest.raises(AgentQuotaError):
        TASK.run(make_context(SideTaskKey.COVER))

    assert load_state(layout).side_tasks.cover is None
    assert not layout.cover.exists()
