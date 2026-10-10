from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from tests.sources.fakes import FakeYtDlp

from grillmaster.core.source_id import Platform, SourceId
from grillmaster.core.stage_key import StageKey
from grillmaster.pipeline.stage import RunOptions
from grillmaster.project.state import ProjectState, Section
from grillmaster.sources.errors import SourceError
from grillmaster.sources.live_chat import LIVE_CHAT_TRACK
from grillmaster.stages import chat_fetch

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

    from tests.stages.conftest import MakeContext

    from grillmaster.config.load import LoadedConfig
    from grillmaster.project.layout import ProjectLayout


def replay_line(offset_ms: int, author: str, text: str) -> str:
    item = {
        "liveChatTextMessageRenderer": {
            "authorName": {"simpleText": author},
            "message": {"runs": [{"text": text}]},
        }
    }
    return json.dumps(
        {
            "replayChatItemAction": {
                "videoOffsetTimeMsec": str(offset_ms),
                "actions": [{"addChatItemAction": {"item": item}}],
            }
        },
        ensure_ascii=False,
    )


REPLAY = (
    "\n".join(
        [
            replay_line(1_000, "a", "before the section"),
            replay_line(1_830_500, "b", "草"),
            replay_line(2_200_000, "c", "after the section"),
        ]
    )
    + "\n"
)


def write_replay(options: Mapping[str, Any]) -> None:
    stem = Path(options["outtmpl"]["default"])
    stem.with_name(f"{stem.name}.{LIVE_CHAT_TRACK}.json").write_text(
        REPLAY, encoding="utf-8"
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
    ytdlp = FakeYtDlp(on_download=write_replay)

    chat_fetch.build(ytdlp).run(make_context(StageKey.CHAT_FETCH))

    assert layout.chat_raw.read_text(encoding="utf-8") == REPLAY
    (call,) = ytdlp.calls
    assert call.url == "https://www.youtube.com/watch?v=stream1"
    messages = json.loads(layout.chat_messages.read_text(encoding="utf-8"))["messages"]
    assert [(message["seconds"], message["text"]) for message in messages] == [
        (30.5, "草")
    ]


def test_a_video_without_replay_fails(make_context: MakeContext, layout: ProjectLayout):
    with pytest.raises(SourceError, match="--chat"):
        chat_fetch.build(FakeYtDlp()).run(make_context(StageKey.CHAT_FETCH))
    assert not layout.chat_messages.exists()


def test_runs_only_with_chat(state: ProjectState):
    assert chat_fetch.STAGE.enabled(RunOptions(source=state.source_id, chat=True))
    assert not chat_fetch.STAGE.enabled(RunOptions(source=state.source_id))


def test_definition(layout: ProjectLayout, loaded: LoadedConfig):
    assert chat_fetch.STAGE.key is StageKey.CHAT_FETCH
    assert chat_fetch.STAGE.outputs(layout) == ()
    assert chat_fetch.STAGE.params(loaded.config) == {"tool": "yt-dlp"}
