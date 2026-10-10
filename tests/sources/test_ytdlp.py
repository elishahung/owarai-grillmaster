from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from tests.fakes import RecordingSink
from tests.sources.fakes import FakeYtDlp
from yt_dlp.utils import DownloadError

from grillmaster.core.source_id import Platform
from grillmaster.events.types import ProgressAdvanced, ProgressFinished, ProgressStarted
from grillmaster.sources.registry import source_platform
from grillmaster.sources.ytdlp import (
    CAPTION_LANGUAGES,
    FRAGMENT_RETRIES,
    DownloadProgress,
    JpegThumbnailFixupPP,
    LoguruYtDlpLogger,
    VideoInfo,
    download_video,
    downloaded_parts,
    fetch_info,
    shared_options,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path
    from typing import Any

JPEG_MAGIC = b"\xff\xd8\xff\xe0" + b"\x00" * 16
PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def clock(*ticks: float) -> Callable[[], float]:
    values = iter(ticks)
    return lambda: next(values)


# --- shared options ----------------------------------------------------------


def test_shared_options_send_the_configured_cookies(tmp_path: Path):
    cookies = tmp_path / "cookies.txt"

    options = shared_options(source_platform(Platform.TVER), cookies)

    assert options["cookiefile"] == str(cookies)
    assert options["noprogress"] is True
    assert isinstance(options["logger"], LoguruYtDlpLogger)


def test_bilibili_never_sends_cookies(tmp_path: Path):
    options = shared_options(source_platform(Platform.BILIBILI), tmp_path / "c.txt")

    assert options["cookiefile"] is None


def test_no_cookies_configured():
    assert shared_options(source_platform(Platform.TVER), None)["cookiefile"] is None


# --- metadata ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1777816800, 1777816800),
        (1777816800.9, 1777816800),
        ("soon", None),
        (True, None),
    ],
)
def test_video_info_epochs_degrade_to_absent(value: object, expected: int | None):
    info = VideoInfo.model_validate({"id": "x", "title": "t", "timestamp": value})
    assert info.timestamp == expected


def test_video_info_program_labels_are_trimmed_or_absent():
    info = VideoInfo.model_validate(
        {
            "id": "x",
            "title": "t",
            "series": " ドキュメンタル ",
            "channel": "  ",
            "extra": 1,
        }
    )
    assert info.series == "ドキュメンタル"
    assert info.channel is None


def test_video_info_filename_keeps_word_characters():
    info = VideoInfo(id="x", title="水曜日のダウンタウン - 第1回！ (前編)")
    assert info.filename == "水曜日のダウンタウン_第1回_前編"


def test_fetch_info_keeps_the_info_json(tmp_path: Path):
    ytdlp = FakeYtDlp(info={"id": "ep1", "title": "番組", "series": "S"})
    info_path = tmp_path / "work" / "info.json"

    info = fetch_info(
        ytdlp, "https://tver.jp/episodes/ep1", info_path, options={"a": 1}
    )

    assert info == VideoInfo(id="ep1", title="番組", series="S")
    assert json.loads(info_path.read_text(encoding="utf-8"))["title"] == "番組"
    (call,) = ytdlp.calls
    assert call.download is False
    assert call.options == {"a": 1}


# --- download ----------------------------------------------------------------


def run_download(
    tmp_path: Path, *, captions: bool = False, ytdlp: FakeYtDlp | None = None
) -> dict[str, Any]:
    ytdlp = ytdlp or FakeYtDlp()
    download_video(
        ytdlp,
        "https://tver.jp/episodes/ep1",
        parts_dir=tmp_path / "parts",
        poster=tmp_path / "poster.jpg",
        options={"cookiefile": None, "noprogress": True},
        captions=captions,
        events=RecordingSink(),
    )
    (call,) = ytdlp.calls
    assert call.download is True
    return call.options


def test_download_layout_and_thumbnail_chain(tmp_path: Path):
    ytdlp = FakeYtDlp()

    options = run_download(tmp_path, ytdlp=ytdlp)

    assert options["outtmpl"] == {
        "default": str(tmp_path / "parts" / "%(playlist_index|0)s.%(ext)s"),
        "thumbnail": str(tmp_path / "poster"),
    }
    assert options["writethumbnail"] is True
    assert options["noprogress"] is True
    assert len(options["progress_hooks"]) == 1
    # The JPEG fixup must run before the thumbnail convertor.
    assert ytdlp.calls[0].before_download == (
        "JpegThumbnailFixupPP",
        "FFmpegThumbnailsConvertorPP",
    )


def test_missing_fragment_aborts_after_backoff_retries(tmp_path: Path):
    options = run_download(tmp_path)

    assert options["skip_unavailable_fragments"] is False
    assert options["fragment_retries"] == FRAGMENT_RETRIES
    sleep = options["retry_sleep_functions"]["fragment"]
    assert [sleep(n) for n in range(7)] == [1, 2, 4, 8, 16, 30, 30]


def test_captions_are_requested_only_when_enabled(tmp_path: Path):
    without = run_download(tmp_path)
    with_captions = run_download(tmp_path, captions=True)

    assert "writesubtitles" not in without
    assert with_captions["writesubtitles"] is True
    assert with_captions["subtitleslangs"] == list(CAPTION_LANGUAGES)
    assert "writeautomaticsub" not in with_captions
    assert with_captions["postprocessors"][-1] == {
        "key": "FFmpegSubtitlesConvertor",
        "format": "srt",
    }


def test_download_failure_propagates_after_one_attempt(tmp_path: Path):
    ytdlp = FakeYtDlp(fail_with=DownloadError("dead"))

    with pytest.raises(DownloadError):
        run_download(tmp_path, ytdlp=ytdlp)


def test_a_failed_download_finishes_its_progress_bar(tmp_path: Path):
    def fail_midway(options: Mapping[str, Any]) -> None:
        options["progress_hooks"][0](progress_update("0.mp4", 40))
        raise DownloadError("connection reset")

    sink = RecordingSink()
    with pytest.raises(DownloadError):
        download_video(
            FakeYtDlp(on_download=fail_midway),
            "u",
            parts_dir=tmp_path / "parts",
            poster=tmp_path / "poster.jpg",
            options={},
            captions=False,
            events=sink,
            clock=clock(1.0),
        )

    assert sink.events == [
        ProgressStarted("download:0.mp4", "Downloading 0.mp4", 1.0),
        ProgressAdvanced("download:0.mp4", 0.4),
        ProgressFinished("download:0.mp4"),
    ]


def test_poster_must_be_a_jpeg(tmp_path: Path):
    with pytest.raises(ValueError, match="JPEG"):
        download_video(
            FakeYtDlp(),
            "u",
            parts_dir=tmp_path,
            poster=tmp_path / "poster.png",
            options={},
            captions=False,
            events=RecordingSink(),
        )


def test_downloaded_parts_are_the_mp4_files_in_name_order(tmp_path: Path):
    for name in (
        "1.mp4",
        "0.mp4",
        "0.ja.srt",
        "0.f30080.mp4.part",
        "0.f299.mp4",
        "0.temp.mp4",
        "full.mp4",
    ):
        (tmp_path / name).write_bytes(b"")

    assert [part.name for part in downloaded_parts(tmp_path)] == ["0.mp4", "1.mp4"]


# --- progress ----------------------------------------------------------------


def progress_update(filename: str, done: int, total: int = 100) -> dict[str, Any]:
    return {
        "status": "downloading",
        "filename": f"out/{filename}",
        "downloaded_bytes": done,
        "total_bytes": total,
    }


def test_progress_is_one_bar_per_file_and_throttled():
    sink = RecordingSink()
    hook = DownloadProgress(sink, clock(1.0, 1.1, 2.0))

    hook(progress_update("0.mp4", 25))
    hook(progress_update("0.mp4", 30))  # inside the throttle window: dropped
    hook(progress_update("0.mp4", 75) | {"speed": 3 * 1024 * 1024, "eta": 12.5})
    hook({"status": "finished", "filename": "out/0.mp4"})

    scope = "download:0.mp4"
    assert sink.events == [
        ProgressStarted(scope, "Downloading 0.mp4", 1.0),
        ProgressAdvanced(scope, 0.25),
        ProgressAdvanced(scope, 0.5, "3.0 MiB/s · eta 12s"),
        ProgressAdvanced(scope, 0.25),  # topped up to 100% on finish
        ProgressFinished(scope),
    ]


def test_a_new_file_finishes_the_previous_bar():
    sink = RecordingSink()
    hook = DownloadProgress(sink, clock(1.0, 2.0))

    hook(progress_update("0.f1.mp4", 50))
    hook(progress_update("0.f2.m4a", 0))

    assert [type(event) for event in sink.events] == [
        ProgressStarted,
        ProgressAdvanced,
        ProgressAdvanced,
        ProgressFinished,
        ProgressStarted,
    ]


def test_progress_without_a_known_size_only_opens_the_bar():
    sink = RecordingSink()
    hook = DownloadProgress(sink, clock(1.0))

    hook(progress_update("0.mp4", 10, total=0))

    assert sink.events == [ProgressStarted("download:0.mp4", "Downloading 0.mp4", 1.0)]


# --- thumbnail fixup ---------------------------------------------------------


def fixup(info: dict[str, Any]) -> dict[str, Any]:
    deleted, info = JpegThumbnailFixupPP().run(info)
    assert deleted == []
    return info


def test_mislabeled_jpeg_png_is_renamed(tmp_path: Path):
    # ABEMA slot thumbnails: JPEG bytes served under a .png name.
    thumb = tmp_path / "0.png"
    thumb.write_bytes(JPEG_MAGIC)

    info = fixup(
        {
            "thumbnails": [{"filepath": str(thumb)}],
            "__files_to_move": {str(thumb): str(tmp_path / "poster.png")},
        }
    )

    jpg = tmp_path / "0.jpg"
    assert not thumb.exists()
    assert jpg.exists()
    assert info["thumbnails"][0]["filepath"] == str(jpg)
    assert info["__files_to_move"] == {str(jpg): str(tmp_path / "poster.jpg")}


@pytest.mark.parametrize(
    ("name", "content"), [("0.png", PNG_MAGIC), ("0.jpg", JPEG_MAGIC)]
)
def test_genuine_or_correct_thumbnails_are_untouched(
    tmp_path: Path, name: str, content: bytes
):
    thumb = tmp_path / name
    thumb.write_bytes(content)

    info = fixup({"thumbnails": [{"filepath": str(thumb)}]})

    assert thumb.exists()
    assert info["thumbnails"][0]["filepath"] == str(thumb)


def test_missing_thumbnail_paths_are_tolerated(tmp_path: Path):
    fixup({"thumbnails": [{}, {"filepath": str(tmp_path / "gone.png")}]})
