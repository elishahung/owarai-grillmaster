from __future__ import annotations

import pytest
from tests.tui.fakes import PLAN, FakeClock

from grillmaster.events.types import RunStarted
from grillmaster.tui.state import PipelineState


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def run_state(clock: FakeClock) -> PipelineState:
    """A state that has seen `RunStarted` for project `epabc123` with `PLAN`."""
    state = PipelineState(clock)
    state.apply(RunStarted("epabc123", PLAN))
    return state
