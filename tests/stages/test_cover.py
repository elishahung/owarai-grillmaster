from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from tests.fakes import FakeAgentRunner
from tests.stages.conftest import complete_side_task

from grillmaster.agents.errors import AgentQuotaError
from grillmaster.config.model import validate_config
from grillmaster.core.stage_key import SideTaskKey, StageKey
from grillmaster.extras.cover import COVER_NAME, POSTER_NAME, TASK_NAME
from grillmaster.extras.errors import ExtrasError
from grillmaster.pipeline.stage import RunOptions
from grillmaster.project.store import load_state
from grillmaster.stages.cover import TASK

if TYPE_CHECKING:
    from pathlib import Path

    from tests.stages.conftest import MakeSideContext

    from grillmaster.agents.runner import AgentRunner
    from grillmaster.agents.task import AgentTask
    from grillmaster.config.load import LoadedConfig
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState


def draws(task: AgentTask[Any]) -> tuple[Path, ...]:
    assert task.workdir is not None
    drawn = task.workdir / COVER_NAME
    drawn.write_bytes(b"png")
    return (drawn,)


@pytest.fixture
def fake_agents(loaded: LoadedConfig) -> FakeAgentRunner:
    return FakeAgentRunner({TASK_NAME: draws}, roles=loaded.config.agents.roles.specs())


@pytest.fixture
def agents(fake_agents: FakeAgentRunner) -> AgentRunner:
    return fake_agents


@pytest.fixture
def poster(layout: ProjectLayout) -> Path:
    layout.poster.write_bytes(b"jpeg")
    return layout.poster


def test_starts_after_the_download_that_fetches_the_poster() -> None:
    assert TASK.key is SideTaskKey.COVER
    assert TASK.start_after is StageKey.DOWNLOAD


@pytest.mark.parametrize(
    ("flag", "feature", "expected"),
    [(False, False, False), (True, False, True), (False, True, True)],
)
def test_enabled_by_the_run_flag_or_the_feature(
    tmp_path: Path,
    state: ProjectState,
    roles: dict[str, str],
    *,
    flag: bool,
    feature: bool,
    expected: bool,
) -> None:
    config = validate_config(
        {"agents": {"roles": roles}, "features": {"cover": feature}}, root=tmp_path
    )
    options = RunOptions(source=state.source_id, cover=flag)

    assert TASK.enabled(options, config) is expected


def test_params_name_the_image_role(loaded: LoadedConfig) -> None:
    assert TASK.params(loaded.config) == {"model": "codex/gpt-5.5/high"}


@pytest.mark.usefixtures("poster")
def test_draws_the_root_cover_in_its_side_directory(
    make_side_context: MakeSideContext,
    layout: ProjectLayout,
    state: ProjectState,
    fake_agents: FakeAgentRunner,
) -> None:
    summary = complete_side_task(TASK, make_side_context(SideTaskKey.COVER))

    assert summary == "cover.png"
    assert layout.cover.read_bytes() == b"png"
    side = layout.side_dir(SideTaskKey.COVER)
    task = fake_agents.task(TASK_NAME)
    assert task.workdir == side
    assert task.session_dir == side / "session"
    assert task.images == (side / POSTER_NAME,)
    record = load_state(layout).side_tasks.cover
    assert record is not None
    assert record.elapsed_s == 1.0
    assert TASK.done(state)


@pytest.mark.usefixtures("poster")
def test_an_existing_cover_is_recorded_without_the_agent(
    make_side_context: MakeSideContext,
    layout: ProjectLayout,
    fake_agents: FakeAgentRunner,
) -> None:
    layout.cover.write_bytes(b"kept")

    complete_side_task(TASK, make_side_context(SideTaskKey.COVER))

    assert fake_agents.tasks == []
    assert load_state(layout).side_tasks.cover is not None


def test_missing_poster_fails_unrecorded(
    make_side_context: MakeSideContext, layout: ProjectLayout
) -> None:
    with pytest.raises(ExtrasError):
        TASK.run(make_side_context(SideTaskKey.COVER))

    assert load_state(layout).side_tasks.cover is None


@pytest.mark.usefixtures("poster")
def test_agent_failure_leaves_the_task_unrecorded(
    make_side_context: MakeSideContext,
    layout: ProjectLayout,
    fake_agents: FakeAgentRunner,
) -> None:
    fake_agents.script[TASK_NAME] = AgentQuotaError("429")

    with pytest.raises(AgentQuotaError):
        TASK.run(make_side_context(SideTaskKey.COVER))

    assert load_state(layout).side_tasks.cover is None
    assert not layout.cover.exists()
