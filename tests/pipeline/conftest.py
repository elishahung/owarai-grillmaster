from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from grillmaster.agents.runner import AgentRunner
from grillmaster.config.load import LoadedConfig
from grillmaster.config.model import validate_config
from grillmaster.config.secrets import Secrets
from grillmaster.events.bus import EventBus
from grillmaster.events.context import install_log_context
from grillmaster.pipeline.stage import RunOptions
from grillmaster.project.layout import ProjectLayout
from grillmaster.project.store import save_state

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fakes import RecordingSink

    from grillmaster.agents.adapters.base import AgentAdapter
    from grillmaster.core.model_spec import Backend
    from grillmaster.project.state import ProjectState


@pytest.fixture(autouse=True)
def _log_context() -> None:
    """Tag log records with their scope, as `grill` does at startup."""
    install_log_context()


@pytest.fixture
def loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, roles: dict[str, str]
) -> LoadedConfig:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    config = validate_config({"agents": {"roles": roles}}, root=tmp_path)
    return LoadedConfig(root=tmp_path, config=config, secrets=Secrets())


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


def _no_adapter(backend: Backend) -> AgentAdapter:
    raise AssertionError(f"pipeline tests run no agents ({backend})")


@pytest.fixture
def agents(bus: EventBus) -> AgentRunner:
    return AgentRunner({}, _no_adapter, max_concurrent=1, timeout_s=1.0, events=bus)
