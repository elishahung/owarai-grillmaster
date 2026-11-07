"""The compilation branches of the metadata and download stages, the date
research it never runs, and the segments the translation context reads."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from tests.fakes import CONTIGUOUS, FAKE_JPEG, FakeFfmpeg, stream_json
from tests.sources.fakes import FakeJsonHttp, FakeYtDlp, write_download

from grillmaster.core.source_id import Platform, SourceId
from grillmaster.core.stage_key import SideTaskKey, StageKey
from grillmaster.core.timecode import TimeRange
from grillmaster.events.types import ProgressAdvanced, ProgressFinished, ProgressStarted
from grillmaster.project.state import Compilation, ProjectState, Segment
from grillmaster.project.store import load_state
from grillmaster.sources.ytdlp import ENTRIES_SCOPE, ENTRY_THUMBNAIL, safe_filename
from grillmaster.stages import date_research, download, metadata
from grillmaster.stages._common import source_context

if TYPE_CHECKING:
    import threading
    from collections.abc import Mapping, Sequence

    from tests.fakes import RecordingSink
    from tests.stages.conftest import MakeContext

    from grillmaster.media.ffmpeg import ProgressCallback
    from grillmaster.project.layout import ProjectLayout

TITLE = "M-1 3回戦 #1"
URL_A = "https://www.youtube.com/watch?v=aaa"
URL_B = "https://www.youtube.com/watch?v=bbb"
INFOS: dict[str, dict[str, Any]] = {
    URL_A: {
        "id": "aaa",
        "title": "A/B",
        "timestamp": 1783515600,  # 2026-07-08 22:00 JST
        "channel": "M-1グランプリ",
    },
    URL_B: {
        "id": "bbb",
        "title": "C/D",
        "timestamp": 1783602000,  # 2026-07-09 22:00 JST
        "channel": "M-1グランプリ",
    },
}
GRILL_TOML = '[agents.roles]\nprepass = "agy/gemini-3.1-pro/high"\n'


@pytest.fixture
def config_sections(tmp_path: Path) -> dict[str, Any]:
    (tmp_path / "grill.toml").write_text(GRILL_TOML, encoding="utf-8")
    return {}


@pytest.fixture
def compilation() -> Compilation:
    return Compilation(title=TITLE, entries=["v=aaa", "v=bbb"])


@pytest.fixture
def state(compilation: Compilation) -> ProjectState:
    return ProjectState.create(
        SourceId(Platform.YOUTUBE, "m1r3e01"), compilation=compilation
    )


def download_with_progress(options: Mapping[str, Any]) -> None:
    """`write_download`, reporting the video stream to the progress hook."""
    (hook,) = options["progress_hooks"]
    filename = str(Path(options["outtmpl"]["default"]).parent / "0.f616.mp4")
    hook(
        {
            "status": "downloading",
            "filename": filename,
            "downloaded_bytes": 1,
            "total_bytes": 2,
            "info_dict": {"vcodec": "vp9", "acodec": "none"},
        }
    )
    hook({"status": "finished", "filename": filename})
    write_download(options)


def entry_ytdlp(infos: dict[str, dict[str, Any]] = INFOS) -> FakeYtDlp:
    """Each entry URL answered with its own info; a download writes `0.mp4`."""
    return FakeYtDlp(by_url=dict(infos), on_download=download_with_progress)


def visited(ytdlp: FakeYtDlp) -> list[tuple[str, bool]]:
    return [(call.url, call.download) for call in ytdlp.calls]


class EntryFfmpeg(FakeFfmpeg):
    """Gap-free audio, `durations` by part directory, and the stream format
    `formats` gives each part directory."""

    def __init__(self, durations: dict[str, float], formats: dict[str, str]) -> None:
        super().__init__(
            CONTIGUOUS, streams=lambda path: formats[Path(path).parent.name]
        )
        self.durations = durations

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        cwd: Path | None = None,
        on_progress: ProgressCallback | None = None,
        abort: threading.Event | None = None,
    ) -> str:
        if "format=duration" in argv:
            super().run(argv, timeout=timeout)
            return f"{self.durations[Path(argv[-1]).parent.name]}\n"
        return super().run(
            argv, timeout=timeout, cwd=cwd, on_progress=on_progress, abort=abort
        )


# --- metadata ---------------------------------------------------------------


def test_metadata_fetches_every_entry_and_names_the_project_by_the_manifest(
    make_context: MakeContext, layout: ProjectLayout
):
    ytdlp = entry_ytdlp()

    metadata.STAGE.run(
        make_context(StageKey.METADATA, ytdlp=ytdlp, http=FakeJsonHttp())
    )

    assert visited(ytdlp) == [(URL_A, False), (URL_B, False)]
    assert (
        json.loads(layout.metadata_entry_info(2).read_text(encoding="utf-8"))["id"]
        == "bbb"
    )
    saved = load_state(layout)
    assert saved.name == safe_filename(TITLE)
    # The newest entry dates the compilation.
    assert saved.broadcast_date == date(2026, 7, 9)
    assert saved.source.title == TITLE
    assert saved.source.channel == "M-1グランプリ"
    assert saved.source.series is None


def test_the_manifest_overrides_the_date_series_and_channel(
    make_context: MakeContext, layout: ProjectLayout, compilation: Compilation
):
    compilation.broadcast_date = date(2026, 11, 1)
    compilation.channel = "M1"
    compilation.series = "M-1 3回戦"

    metadata.STAGE.run(make_context(StageKey.METADATA, ytdlp=entry_ytdlp()))

    saved = load_state(layout)
    assert saved.broadcast_date == date(2026, 11, 1)
    assert (saved.source.series, saved.source.channel) == ("M-1 3回戦", "M1")


def test_a_channel_the_entries_do_not_share_is_left_unset(
    make_context: MakeContext, layout: ProjectLayout
):
    infos = {**INFOS, URL_B: {**INFOS[URL_B], "channel": "Other"}}

    metadata.STAGE.run(make_context(StageKey.METADATA, ytdlp=entry_ytdlp(infos)))

    assert load_state(layout).source.channel is None


# --- download ---------------------------------------------------------------


def write_entry_infos(layout: ProjectLayout) -> None:
    for number, url in enumerate((URL_A, URL_B), 1):
        path = layout.metadata_entry_info(number)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(INFOS[url]), encoding="utf-8")


def test_download_records_the_segments_then_joins_the_entries(
    make_context: MakeContext,
    layout: ProjectLayout,
    recording_sink: RecordingSink,
):
    write_entry_infos(layout)
    ffmpeg = EntryFfmpeg(
        {"01": 300.5, "02": 200.0},
        {
            "01": stream_json(codec="av1"),
            "02": stream_json(codec="vp9", fps="30000/1001"),
        },
    )
    ytdlp = entry_ytdlp()

    download.STAGE.run(make_context(StageKey.DOWNLOAD, ytdlp=ytdlp, ffmpeg=ffmpeg))

    assert visited(ytdlp) == [(URL_A, True), (URL_B, True)]
    assert ytdlp.calls[1].options["format_sort"] == ["res", "vcodec:vp9"]
    assert load_state(layout).segments == [
        Segment(title="A/B", start=0.0, end=300.5),
        Segment(title="C/D", start=300.5, end=500.5),
    ]
    # Different formats: one re-encode, the parts consumed.
    join = ffmpeg.calls[-1]
    assert "-filter_complex" in join
    assert "hevc_nvenc" in join
    assert layout.full_video.read_bytes() == FAKE_JPEG
    assert not (layout.download_entry_dir(1) / "0.mp4").exists()
    # The first entry's thumbnail is the poster; the others stay beside their parts.
    assert layout.poster.read_bytes() == b"jpeg"
    assert (layout.download_entry_dir(2) / ENTRY_THUMBNAIL).exists()
    (bar,) = [
        e
        for e in recording_sink.events
        if isinstance(e, ProgressStarted) and e.scope == "download:join"
    ]
    assert bar.total == pytest.approx(500.5)
    # One step of the Videos bar per entry, naming it.
    videos = [
        e for e in recording_sink.events if getattr(e, "scope", None) == ENTRIES_SCOPE
    ]
    assert videos == [
        ProgressStarted(ENTRIES_SCOPE, "Videos", 2),
        ProgressAdvanced(ENTRIES_SCOPE, 1, "done 1/2 · A/B"),
        ProgressAdvanced(ENTRIES_SCOPE, 1, "done 2/2 · C/D"),
        ProgressFinished(ENTRIES_SCOPE),
    ]
    # Each file's bar names its entry, not yt-dlp's `0.f616.mp4`.
    files = [
        e.label
        for e in recording_sink.events
        if isinstance(e, ProgressStarted)
        and e.scope.startswith("download:")
        and e.scope not in {ENTRIES_SCOPE, "download:join"}
    ]
    assert files == [
        "Downloading 1/2 · A/B (video)",
        "Downloading 2/2 · C/D (video)",
    ]


def test_an_existing_full_video_is_the_finished_compilation_download(
    make_context: MakeContext, layout: ProjectLayout
):
    layout.full_video.parent.mkdir(parents=True)
    layout.full_video.write_bytes(b"done")
    ytdlp = entry_ytdlp()

    download.STAGE.run(make_context(StageKey.DOWNLOAD, ytdlp=ytdlp))

    assert visited(ytdlp) == []


def test_a_reset_of_the_download_clears_the_segments(state: ProjectState):
    state.segments = [Segment(title="A", start=0, end=1)]
    download.STAGE.clear_state(state)
    assert state.segments == []


# --- side tasks and translation context -------------------------------------


def test_date_research_never_applies_to_a_compilation(make_context: MakeContext):
    applies = date_research.TASK.applies
    assert applies is not None
    assert not applies(make_context(SideTaskKey.DATE_RESEARCH))


def test_the_translation_context_carries_the_segments(
    make_context: MakeContext, state: ProjectState
):
    state.segments = [
        Segment(title="A/B", start=0.0, end=300.5),
        Segment(title="C/D", start=300.5, end=500.5),
    ]

    context = source_context(
        make_context(StageKey.PREPASS), StageKey.PREPASS, with_parent=False
    )

    assert [(s.title, s.span) for s in context.segments] == [
        ("A/B", TimeRange(0.0, 300.5)),
        ("C/D", TimeRange(300.5, 500.5)),
    ]
