from __future__ import annotations

import json
from functools import partial
from typing import TYPE_CHECKING

import pytest
from tests.fakes import replay_line, text_item, write_replay
from tests.sources.fakes import FakeYtDlp

from grillmaster.core.source_id import Platform, SourceId
from grillmaster.core.stage_key import StageKey
from grillmaster.project.state import ProjectState, Section
from grillmaster.sources.errors import SourceError
from grillmaster.stages import chat_fetch

if TYPE_CHECKING:
    from tests.stages.conftest import MakeContext

    from grillmaster.project.layout import ProjectLayout


REPLAY = (
    "\n".join(
        [
            replay_line(1_000, text_item("a", {"text": "before the section"})),
            replay_line(1_830_500, text_item("b", {"text": "草"})),
            replay_line(2_200_000, text_item("c", {"text": "after the section"})),
        ]
    )
    + "\n"
)


@pytest.fixture
def state() -> ProjectState:
    """A YouTube project combine already cut to 1800-2100s."""
    project = ProjectState.create(SourceId(Platform.YOUTUBE, "v=stream1"))
    project.section = Section(start=1800.0, end=2100.0)
    return project


def test_fetches_the_replay_and_rebases_it_on_the_recorded_section(
    make_context: MakeContext, layout: ProjectLayout
):
    ytdlp = FakeYtDlp(on_download=partial(write_replay, replay=REPLAY))

    chat_fetch.STAGE.run(make_context(StageKey.CHAT_FETCH, ytdlp=ytdlp))

    assert layout.chat_raw.read_text(encoding="utf-8") == REPLAY
    (call,) = ytdlp.calls
    assert call.url == "https://www.youtube.com/watch?v=stream1"
    messages = json.loads(layout.chat_messages.read_text(encoding="utf-8"))["messages"]
    assert [(message["seconds"], message["text"]) for message in messages] == [
        (30.5, "草")
    ]


def test_a_video_without_replay_fails(make_context: MakeContext, layout: ProjectLayout):
    with pytest.raises(SourceError, match="--chat"):
        chat_fetch.STAGE.run(make_context(StageKey.CHAT_FETCH))
    assert not layout.chat_messages.exists()
