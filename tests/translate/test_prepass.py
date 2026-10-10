from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.fakes import FakeAgentRunner
from tests.translate.conftest import briefing

from grillmaster.agents.adapters.base import Capability
from grillmaster.agents.errors import ValidationFailure
from grillmaster.agents.schema import strict_json_schema
from grillmaster.agents.task import AgentTask, SchemaOutput
from grillmaster.core.briefing import Briefing
from grillmaster.core.json_artifact import read_model, write_model
from grillmaster.core.model_spec import Role
from grillmaster.translate.prepass import (
    TASK_NAME,
    build_prepass_task,
    ensure_briefing,
    segment_coverage_validator,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from grillmaster.core.tool_session import ToolSession
    from grillmaster.translate.inputs import PrepassInputs


def prepass_task(session_dir: Path) -> AgentTask[Briefing]:
    return AgentTask(
        name=TASK_NAME,
        role=Role.PREPASS,
        instructions="",
        prompt="",
        session_dir=session_dir,
        workdir=None,
        output=SchemaOutput(Briefing),
    )


def test_ensure_briefing_analyses_and_writes_on_a_miss(tmp_path: Path) -> None:
    output = tmp_path / "briefing.json"
    agents = FakeAgentRunner({TASK_NAME: briefing((1, 6))})

    result = ensure_briefing(output, agents, lambda: prepass_task(tmp_path))

    assert result == briefing((1, 6))
    assert read_model(output, Briefing) == result


def test_ensure_briefing_reuses_the_file_without_preparing(tmp_path: Path) -> None:
    output = tmp_path / "briefing.json"
    write_model(output, briefing((1, 6)))
    prepared: list[bool] = []

    def prepare() -> AgentTask[Briefing]:
        prepared.append(True)
        return prepass_task(tmp_path)

    assert ensure_briefing(output, FakeAgentRunner(), prepare) == briefing((1, 6))
    assert prepared == []


def test_ensure_briefing_redoes_an_unreadable_file(tmp_path: Path) -> None:
    output = tmp_path / "briefing.json"
    output.write_text("{ truncated", encoding="utf-8")
    agents = FakeAgentRunner({TASK_NAME: briefing((1, 6))})

    ensure_briefing(output, agents, lambda: prepass_task(tmp_path))

    assert read_model(output, Briefing) == briefing((1, 6))


def test_full_coverage_is_accepted() -> None:
    validate = segment_coverage_validator([(1, 115), (116, 233)])
    validate(briefing((1, 115), (116, 233)))


def test_missing_ranges_are_rejected_and_named() -> None:
    validate = segment_coverage_validator([(1, 115), (116, 233)])
    with pytest.raises(ValidationFailure) as caught:
        validate(briefing((1, 115)))
    message = str(caught.value)
    assert "1/2" in message
    # Only the uncovered range is quoted back for the repair.
    assert '"from_index": 116' in message
    assert '"from_index": 1,' not in message


def test_shifted_range_does_not_count() -> None:
    with pytest.raises(ValidationFailure):
        segment_coverage_validator([(1, 115)])(briefing((1, 116)))


def test_briefing_is_native_schema_compatible() -> None:
    strict_json_schema(Briefing)


def test_build_prepass_task(
    tmp_path: Path,
    prepass_inputs: Callable[..., PrepassInputs],
    tools: ToolSession,
) -> None:
    task = build_prepass_task(
        prepass_inputs(),
        session_dir=tmp_path / "session",
        workdir=tmp_path,
        tools=tools,
        add_dirs=(tmp_path / "audio",),
    )
    assert task.name == "prepass"
    assert task.role is Role.PREPASS
    assert isinstance(task.output, SchemaOutput)
    assert task.requires == frozenset({Capability.WEB_SEARCH})
    assert task.images == (tmp_path / "f1.jpg",)
    assert task.audio == (tmp_path / "audio.ogg",)
    assert task.add_dirs == (tmp_path / "audio",)
    assert "Full Source Audio" in task.instructions
    assert task.validate is not None
    # The inputs have chunks 1-3 and 4-6; a briefing covering one fails.
    with pytest.raises(ValidationFailure):
        task.validate(briefing((1, 3)))
    task.validate(briefing((1, 3), (4, 6)))


def test_build_prepass_task_without_audio(
    tmp_path: Path,
    prepass_inputs: Callable[..., PrepassInputs],
    tools: ToolSession,
) -> None:
    task = build_prepass_task(
        prepass_inputs(audio=False),
        session_dir=tmp_path / "session",
        workdir=tmp_path,
        tools=tools,
    )
    assert task.audio == ()
    assert "Full Source Audio" not in task.instructions
    assert "No audio track is available for this run" in task.instructions
