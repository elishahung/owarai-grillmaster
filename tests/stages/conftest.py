"""Shared stage-test fixtures: a real project layout and state in `tmp_path`,
a minimal config, and `make_context` to build a `StageContext` for a stage.

Override `secrets` (or `agents`) in a test module to change what the
context carries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.fakes import RecordingSink

from grillmaster.agents.runner import AgentRunner
from grillmaster.config.load import LoadedConfig
from grillmaster.config.model import validate_config
from grillmaster.config.secrets import Secrets
from grillmaster.core.source_id import Platform, SourceId
from grillmaster.events.bus import EventBus
from grillmaster.pipeline.stage import RunOptions, StageContext, StateStore
from grillmaster.pipeline.steps import StepOutcome
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.state import ProjectState
from grillmaster.project.store import save_state

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from grillmaster.agents.adapters.base import AgentAdapter
    from grillmaster.core.model_spec import Backend
    from grillmaster.core.stage_key import SideTaskKey, StageKey
    from grillmaster.pipeline.side_tasks import SideTaskDef

    type MakeContext = Callable[[StageKey], StageContext]
    type MakeSideContext = Callable[[SideTaskKey], StageContext]

ROLES = {
    "prepass": "agy/gemini-3.1-pro/high",
    "chunk": "agy/gemini-3.1-pro",
    "postprocess": "codex/gpt-5.6-sol/medium",
    "utility": "codex/gpt-5.5/medium",
    "image": "codex/gpt-5.5/high",
}


@pytest.fixture
def secrets(monkeypatch: pytest.MonkeyPatch) -> Secrets:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    return Secrets()


@pytest.fixture
def loaded(tmp_path: Path, secrets: Secrets) -> LoadedConfig:
    config = validate_config({"agents": {"roles": ROLES}}, root=tmp_path)
    return LoadedConfig(root=tmp_path, config=config, secrets=secrets)


@pytest.fixture
def state() -> ProjectState:
    return ProjectState.create(SourceId(Platform.TVER, "epabc123"))


@pytest.fixture
def layout(loaded: LoadedConfig, state: ProjectState) -> ProjectLayout:
    project = ProjectLayout.for_id(loaded.projects_root, state.id)
    save_state(project, state)
    return project


@pytest.fixture
def options(state: ProjectState) -> RunOptions:
    return RunOptions(source=state.source_id)


@pytest.fixture
def recording() -> RecordingSink:
    return RecordingSink()


@pytest.fixture
def bus(recording: RecordingSink) -> EventBus:
    return EventBus([recording])


def _no_adapter(backend: Backend) -> AgentAdapter:
    raise AssertionError(f"this stage test runs no agents ({backend})")


@pytest.fixture
def agents(bus: EventBus) -> AgentRunner:
    return AgentRunner({}, _no_adapter, max_concurrent=1, timeout_s=1.0, events=bus)


@pytest.fixture
def make_context(
    *,
    layout: ProjectLayout,
    state: ProjectState,
    loaded: LoadedConfig,
    options: RunOptions,
    agents: AgentRunner,
    bus: EventBus,
) -> MakeContext:
    """`StageContext` for stage `key`, its workdir `work/NN_<key>/`."""
    store = StateStore(layout, state)

    def make(key: StageKey) -> StageContext:
        return StageContext(
            layout=layout,
            store=store,
            loaded=loaded,
            options=options,
            agents=agents,
            events=bus,
            workdir=layout.work_dir(key),
        )

    return make


@pytest.fixture
def make_side_context(
    *,
    layout: ProjectLayout,
    state: ProjectState,
    loaded: LoadedConfig,
    options: RunOptions,
    agents: AgentRunner,
    bus: EventBus,
) -> MakeSideContext:
    """`StageContext` for side task `key`, its workdir `work/side/<key>/`."""
    store = StateStore(layout, state)

    def make(key: SideTaskKey) -> StageContext:
        return StageContext(
            layout=layout,
            store=store,
            loaded=loaded,
            options=options,
            agents=agents,
            events=bus,
            workdir=layout.side_dir(key),
        )

    return make


def complete_side_task[T](task: SideTaskDef[T], ctx: StageContext) -> str | None:
    """Run `task` and record it as `SideTaskManager` does (elapsed 1s, no
    agent usage collected); returns the `StepCompleted` text."""
    outcome = StepOutcome(task.run(ctx), elapsed=1.0, usage=None)
    ctx.update(lambda state: task.store(state, outcome))
    return task.describe(outcome.value)
