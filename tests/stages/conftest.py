"""Stage-test fixtures: a real project layout and state in `tmp_path`, a
config built from `config_data`, a scripted `FakeAgentRunner`, fake process
seams, and `make_context` to build a step's `StageContext`.

Override `config_sections` (`grill.toml` sections), `script` (agent outputs
by task name), `secrets`, `fake_ffmpeg`, `ytdlp`, `http` or `speech_to_text`
in a test module to change what the context carries; parametrize
`config_data` indirectly for a variant; `make_context(key, ffmpeg=...)`
swaps one seam for a call.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest
from tests.fakes import FakeAgentRunner

from grillmaster.config.load import LoadedConfig
from grillmaster.config.model import validate_config
from grillmaster.config.secrets import Secrets
from grillmaster.core.stage_key import StageKey
from grillmaster.events.bus import EventBus
from grillmaster.pipeline.state_store import StateStore
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.store import save_state
from grillmaster.stages.base import RunOptions, StageContext, StepOutcome

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from tests.fakes import RecordingSink

    from grillmaster.core.stage_key import SideTaskKey
    from grillmaster.project.state import ProjectState
    from grillmaster.stages.base import Externals, SideTaskDef

    type MakeContext = Callable[..., StageContext]


@pytest.fixture
def secrets(monkeypatch: pytest.MonkeyPatch) -> Secrets:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    return Secrets()


@pytest.fixture
def config_sections() -> dict[str, Any]:
    """`grill.toml` sections besides `[agents.roles]`; modules override it."""
    return {}


@pytest.fixture
def config_data(
    request: pytest.FixtureRequest,
    roles: dict[str, str],
    config_sections: dict[str, Any],
) -> dict[str, Any]:
    """The `grill.toml` data: `roles` and `config_sections`.

    Parametrize it indirectly with overrides: entries under `roles` replace
    single roles, any other key replaces that whole section.
    """
    overrides = dict(getattr(request, "param", {}))
    role_overrides = overrides.pop("roles", {})
    return {
        "agents": {"roles": {**roles, **role_overrides}},
        **config_sections,
        **overrides,
    }


@pytest.fixture
def loaded(
    tmp_path: Path, secrets: Secrets, config_data: dict[str, Any]
) -> LoadedConfig:
    config = validate_config(config_data, root=tmp_path)
    return LoadedConfig(root=tmp_path, config=config, secrets=secrets)


@pytest.fixture
def layout(loaded: LoadedConfig, state: ProjectState) -> ProjectLayout:
    project = ProjectLayout.for_id(loaded.projects_root, state.id)
    save_state(project, state)
    return project


@pytest.fixture
def options(state: ProjectState) -> RunOptions:
    return RunOptions(source=state.source_id)


@pytest.fixture
def bus(recording_sink: RecordingSink) -> EventBus:
    return EventBus([recording_sink])


@pytest.fixture
def script() -> dict[str, object]:
    """Agent outputs by task name (see `FakeAgentRunner`); none by default."""
    return {}


@pytest.fixture
def agents(
    loaded: LoadedConfig, script: dict[str, object], bus: EventBus
) -> FakeAgentRunner:
    return FakeAgentRunner(script, roles=loaded.config.agents.roles.specs(), events=bus)


@pytest.fixture
def make_context(
    *,
    layout: ProjectLayout,
    state: ProjectState,
    loaded: LoadedConfig,
    options: RunOptions,
    agents: FakeAgentRunner,
    bus: EventBus,
    externals: Externals,
) -> MakeContext:
    """`StageContext` for stage `key` (workdir `work/NN_<key>/`) or side task
    `key` (`work/side/<key>/`); keyword arguments replace `Externals` seams."""
    store = StateStore(layout, state)

    def make(key: StageKey | SideTaskKey, **seams: Any) -> StageContext:
        workdir = (
            layout.work_dir(key) if isinstance(key, StageKey) else layout.side_dir(key)
        )
        return StageContext(
            layout=layout,
            store=store,
            loaded=loaded,
            options=options,
            agents=agents,
            events=bus,
            externals=replace(externals, **seams),
            workdir=workdir,
        )

    return make


def complete_side_task[T](task: SideTaskDef[T], ctx: StageContext) -> str | None:
    """Run `task` and record it as `SideTaskManager` does (elapsed 1s, no
    agent usage collected); returns the `StepCompleted` text."""
    outcome = StepOutcome(task.run(ctx), elapsed=1.0, usage=None)
    ctx.update(lambda state: task.store(state, outcome))
    return task.describe(outcome.value)
