from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from tests.agents.fakes import RecordingSink

from grillmaster.agents.adapters.base import TurnRequest
from grillmaster.core.model_spec import Backend, Effort, ModelSpec

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.fixture
def recording_sink() -> RecordingSink:
    return RecordingSink()


@pytest.fixture
def make_request(tmp_path: Path) -> Callable[..., TurnRequest]:
    """A `TurnRequest` rooted in `tmp_path`; keyword overrides win. A
    `schema` is also written as `schema_path`, as the runner does."""

    def make(**overrides: Any) -> TurnRequest:
        workdir = tmp_path / "work"
        session_dir = tmp_path / "session"
        workdir.mkdir(exist_ok=True)
        session_dir.mkdir(exist_ok=True)
        raw: list[str] = []
        fields: dict[str, Any] = {
            "message": "hello",
            "spec": ModelSpec(Backend.CODEX, "gpt-test", Effort.HIGH),
            "workdir": workdir,
            "session_dir": session_dir,
            "timeout_s": 60.0,
            "raw": raw.append,
        }
        fields.update(overrides)
        if fields.get("schema") is not None and "schema_path" not in fields:
            schema_path = session_dir / "schema.json"
            schema_path.write_text(json.dumps(fields["schema"]), encoding="utf-8")
            fields["schema_path"] = schema_path
        return TurnRequest(**fields)

    return make
