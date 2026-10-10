from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from tests.fakes import FakeFfmpeg, RecordingSink
from tests.sources.fakes import FakeJsonHttp, FakeYtDlp

from grillmaster.core.source_id import Platform, SourceId
from grillmaster.legacy.services.program_config import config as program_config
from grillmaster.project.state import ProjectState
from grillmaster.stages.base import Externals

if TYPE_CHECKING:
    from grillmaster.asr.client import SpeechToText, SpeechToTextFactory


@pytest.fixture(autouse=True)
def _isolate_program_config(tmp_path):
    """Keep tests off the maintainer's real `config.json` in the repo root.

    A safety net only: tests that write rules patch `config_path` themselves
    so they stay safe under plain `unittest` too.
    """
    with patch.object(
        program_config, "config_path", return_value=tmp_path / "config.json"
    ):
        yield


@pytest.fixture
def fake_ffmpeg() -> FakeFfmpeg:
    return FakeFfmpeg()


@pytest.fixture
def ytdlp() -> FakeYtDlp:
    return FakeYtDlp()


@pytest.fixture
def http() -> FakeJsonHttp:
    return FakeJsonHttp()


def _no_speech_to_text(_api_key: str) -> SpeechToText:
    raise AssertionError("this test reaches no ASR")


@pytest.fixture
def speech_to_text() -> SpeechToTextFactory:
    return _no_speech_to_text


@pytest.fixture
def externals(
    fake_ffmpeg: FakeFfmpeg,
    ytdlp: FakeYtDlp,
    http: FakeJsonHttp,
    speech_to_text: SpeechToTextFactory,
) -> Externals:
    """Fake process seams; override `fake_ffmpeg`, `ytdlp`, `http` or
    `speech_to_text` to change one."""
    return Externals(
        ffmpeg=fake_ffmpeg, ytdlp=ytdlp, http=http, speech_to_text=speech_to_text
    )


@pytest.fixture
def recording_sink() -> RecordingSink:
    return RecordingSink()


@pytest.fixture
def roles() -> dict[str, str]:
    """A valid `[agents.roles]` table."""
    return {
        "prepass": "agy/gemini-3.1-pro/high",
        "chunk": "agy/gemini-3.1-pro",
        "postprocess": "codex/gpt-5.6-sol/medium",
        "utility": "codex/gpt-5.5/medium",
        "image": "codex/gpt-5.5/high",
    }


@pytest.fixture
def roles_toml(roles: dict[str, str]) -> str:
    """`[agents.roles]` text equal to the `roles` fixture."""
    lines = [f'{role} = "{spec}"' for role, spec in roles.items()]
    return "\n".join(["[agents.roles]", *lines]) + "\n"


@pytest.fixture
def state() -> ProjectState:
    """A fresh TVer project, `epabc123`."""
    return ProjectState.create(SourceId(Platform.TVER, "epabc123"))
