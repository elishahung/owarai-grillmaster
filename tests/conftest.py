from __future__ import annotations

from unittest.mock import patch

import pytest
from tests.fakes import FakeFfmpeg

from grillmaster.legacy.services.program_config import config as program_config


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
