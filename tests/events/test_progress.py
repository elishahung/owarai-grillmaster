from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from grillmaster.events.progress import track
from grillmaster.events.types import ProgressAdvanced, ProgressFinished, ProgressStarted

if TYPE_CHECKING:
    from tests.fakes import RecordingSink


def test_a_bar_finishes_when_the_body_returns(recording_sink: RecordingSink):
    with track(recording_sink, "chunks", "chunks", 2) as advance:
        advance()
        advance(0.5, "half")
    assert recording_sink.events == [
        ProgressStarted("chunks", "chunks", 2),
        ProgressAdvanced("chunks", 1.0, None),
        ProgressAdvanced("chunks", 0.5, "half"),
        ProgressFinished("chunks"),
    ]


def test_a_failing_body_leaves_the_bar_open(recording_sink: RecordingSink):
    def fail_midway() -> None:
        with track(recording_sink, "dl", "Downloading", None) as advance:
            advance()
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        fail_midway()
    assert recording_sink.events == [
        ProgressStarted("dl", "Downloading", None),
        ProgressAdvanced("dl", 1.0, None),
    ]
