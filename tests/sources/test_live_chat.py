from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from tests.sources.fakes import FakeYtDlp

from grillmaster.sources.errors import SourceError
from grillmaster.sources.live_chat import LIVE_CHAT_TRACK, download_live_chat

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

REPLAY = '{"replayChatItemAction": {"videoOffsetTimeMsec": "1000"}}\n'


def write_replay(options: Mapping[str, Any]) -> None:
    stem = Path(options["outtmpl"]["default"])
    stem.with_name(f"{stem.name}.{LIVE_CHAT_TRACK}.json").write_text(
        REPLAY, encoding="utf-8"
    )


def test_downloads_the_replay_track_only(tmp_path: Path):
    output = tmp_path / "chat" / "live_chat.jsonl"
    ytdlp = FakeYtDlp(on_download=write_replay)

    download_live_chat(
        ytdlp, "https://youtu.be/x", output, options={"noprogress": True}
    )

    assert output.read_text(encoding="utf-8") == REPLAY
    (call,) = ytdlp.calls
    assert call.options["skip_download"] is True
    assert call.options["subtitleslangs"] == [LIVE_CHAT_TRACK]
    assert call.options["noprogress"] is True


def test_an_existing_replay_is_reused(tmp_path: Path):
    output = tmp_path / "live_chat.jsonl"
    output.write_text(REPLAY, encoding="utf-8")
    ytdlp = FakeYtDlp()

    download_live_chat(ytdlp, "https://youtu.be/x", output, options={})

    assert ytdlp.calls == []


def test_a_video_without_replay_fails(tmp_path: Path):
    output = tmp_path / "live_chat.jsonl"

    with pytest.raises(SourceError, match="--chat"):
        download_live_chat(FakeYtDlp(), "https://youtu.be/x", output, options={})
    assert not output.exists()
