from __future__ import annotations

import dataclasses
from typing import get_args

import pytest

from grillmaster.events import types
from grillmaster.events.types import Event, PlanEntry

EVENT_CLASSES: tuple[type, ...] = get_args(Event.__value__)


def test_every_documented_event_is_in_the_union():
    assert {cls.__name__ for cls in EVENT_CLASSES} == {
        "RunStarted",
        "RunFinished",
        "BatchItemStarted",
        "StepStarted",
        "StepCompleted",
        "StepSkipped",
        "StepFailed",
        "ProgressStarted",
        "ProgressAdvanced",
        "ProgressFinished",
        "AgentSessionStarted",
        "AgentActivity",
        "AgentSessionFinished",
        "LogLine",
    }
    # A new event dataclass must join the union, or sinks never see it.
    declared = {
        value
        for value in vars(types).values()
        if dataclasses.is_dataclass(value) and value is not PlanEntry
    }
    assert declared == set(EVENT_CLASSES)


@pytest.mark.parametrize("cls", EVENT_CLASSES, ids=lambda cls: cls.__name__)
def test_events_are_frozen_slotted_dataclasses(cls: type):
    assert dataclasses.is_dataclass(cls)
    assert cls.__dataclass_params__.frozen  # pyright: ignore[reportAttributeAccessIssue]
    assert "__slots__" in vars(cls)
    names = {field.name for field in dataclasses.fields(cls)}
    # JsonlSink owns these keys.
    assert not names & {"ts", "type"}
