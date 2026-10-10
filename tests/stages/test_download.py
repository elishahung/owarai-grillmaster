from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, override

import pytest
from tests.fakes import FAKE_JPEG, FakeFfmpeg
from tests.sources.fakes import FakeYtDlp

from grillmaster.core.stage_key import StageKey
from grillmaster.media.errors import MediaError
from grillmaster.project.state import SourceInfo
from grillmaster.sources.errors import SourceError
from grillmaster.stages import download

if TYPE_CHECKING:
    import threading
    from collections.abc import Mapping, Sequence
    from typing import Any

    from tests.stages.conftest import MakeContext

    from grillmaster.config.load import LoadedConfig
    from grillmaster.media.ffmpeg import ProgressCallback
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState

GRILL_TOML = '[agents.roles]\nprepass = "agy/gemini-3.1-pro/high"\n'
CONTIGUOUS = "0.000000\n0.021333\n0.042667\n"


@pytest.fixture
def config_sections(tmp_path: Path) -> dict[str, Any]:
    """Cookies configured, and a real `grill.toml` to register programs in."""
    (tmp_path / "grill.toml").write_text(GRILL_TOML, encoding="utf-8")
    return {"paths": {"cookies": "cookies.txt"}}


def write_download(options: Mapping[str, Any]) -> None:
    """Play yt-dlp: one part plus the poster, at the templated paths."""
    template = Path(options["outtmpl"]["default"])
    template.parent.mkdir(parents=True, exist_ok=True)
    (template.parent / "0.mp4").write_bytes(b"video")
    Path(options["outtmpl"]["thumbnail"]).with_suffix(".jpg").write_bytes(b"jpeg")


@pytest.fixture
def ytdlp() -> FakeYtDlp:
    return FakeYtDlp(on_download=write_download)


def test_downloads_parts_and_poster_and_registers_the_program(
    make_context: MakeContext,
    layout: ProjectLayout,
    loaded: LoadedConfig,
    ytdlp: FakeYtDlp,
    fake_ffmpeg: FakeFfmpeg,
):
    fake_ffmpeg.stdout = CONTIGUOUS
    ctx = make_context(StageKey.DOWNLOAD)
    ctx.state.source = SourceInfo(series="ドキュメンタル", channel="Prime Video")

    download.STAGE.run(ctx)

    assert layout.full_video.read_bytes() == b"video"
    assert not (layout.download_parts_dir / "0.mp4").exists()
    assert layout.poster.read_bytes() == b"jpeg"
    (call,) = ytdlp.calls
    assert call.url == "https://tver.jp/episodes/epabc123"
    assert call.options["cookiefile"] == str(loaded.root / "cookies.txt")
    assert call.options["writesubtitles"] is True  # official subtitles default on
    (probe,) = fake_ffmpeg.calls
    assert probe[0] == "ffprobe"
    assert probe[-1] == str(layout.download_parts_dir / "0.mp4")
    toml = loaded.path.read_text(encoding="utf-8")
    assert toml.startswith(GRILL_TOML)
    assert '[programs.series."ドキュメンタル"]' in toml
    assert '[programs.channel."Prime Video"]' in toml


def test_without_program_names_nothing_is_registered(
    make_context: MakeContext, loaded: LoadedConfig, fake_ffmpeg: FakeFfmpeg
):
    fake_ffmpeg.stdout = CONTIGUOUS

    download.STAGE.run(make_context(StageKey.DOWNLOAD))

    assert loaded.path.read_text(encoding="utf-8") == GRILL_TOML


def test_a_gapped_part_fails_and_names_the_file(
    make_context: MakeContext,
    loaded: LoadedConfig,
    fake_ffmpeg: FakeFfmpeg,
    state: ProjectState,
):
    fake_ffmpeg.stdout = "141.781\n150.140\n"
    state.source = SourceInfo(series="ドキュメンタル")

    with pytest.raises(
        SourceError, match=r"0\.mp4 is missing audio at 141\.78s→150\.14s"
    ):
        download.STAGE.run(make_context(StageKey.DOWNLOAD))
    assert loaded.path.read_text(encoding="utf-8") == GRILL_TOML


def write_two_parts(options: Mapping[str, Any]) -> None:
    """Play yt-dlp: two parts plus one of its format intermediates."""
    parts_dir = Path(options["outtmpl"]["default"]).parent
    parts_dir.mkdir(parents=True, exist_ok=True)
    for name in ("0.mp4", "1.mp4", "1.f299.mp4"):
        (parts_dir / name).write_bytes(b"video")


def test_parts_are_joined_atomically_then_deleted(
    make_context: MakeContext, layout: ProjectLayout, fake_ffmpeg: FakeFfmpeg
):
    fake_ffmpeg.stdout = CONTIGUOUS
    ytdlp = FakeYtDlp(on_download=write_two_parts)

    download.STAGE.run(make_context(StageKey.DOWNLOAD, ytdlp=ytdlp))

    *probes, concat = fake_ffmpeg.calls
    assert [probe[-1] for probe in probes] == [
        str(layout.download_parts_dir / name) for name in ("0.mp4", "1.mp4")
    ]
    assert concat[-1] == str(layout.full_video.with_name(".full.mp4.partial"))
    assert layout.full_video.exists()
    assert sorted(p.name for p in layout.download_parts_dir.iterdir()) == ["1.f299.mp4"]


class DyingConcat(FakeFfmpeg):
    """Writes half the joined video, then dies like an interrupted ffmpeg."""

    @override
    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        cwd: Path | None = None,
        on_progress: ProgressCallback | None = None,
        abort: threading.Event | None = None,
    ) -> str:
        if "concat" in argv:
            Path(argv[-1]).write_bytes(b"trunc")
            raise MediaError("killed")
        return super().run(
            argv, timeout=timeout, cwd=cwd, on_progress=on_progress, abort=abort
        )


def test_an_interrupted_join_is_never_reused(
    make_context: MakeContext, layout: ProjectLayout, fake_ffmpeg: FakeFfmpeg
):
    ytdlp = FakeYtDlp(on_download=write_two_parts)

    with pytest.raises(MediaError, match="killed"):
        download.STAGE.run(
            make_context(StageKey.DOWNLOAD, ytdlp=ytdlp, ffmpeg=DyingConcat(CONTIGUOUS))
        )
    assert not layout.full_video.exists()
    assert (layout.download_parts_dir / "0.mp4").exists()

    fake_ffmpeg.stdout = CONTIGUOUS
    download.STAGE.run(make_context(StageKey.DOWNLOAD, ytdlp=ytdlp))
    assert layout.full_video.read_bytes() == FAKE_JPEG


def test_an_existing_full_video_skips_the_download(
    make_context: MakeContext,
    layout: ProjectLayout,
    ytdlp: FakeYtDlp,
    fake_ffmpeg: FakeFfmpeg,
):
    layout.full_video.parent.mkdir(parents=True)
    layout.full_video.write_bytes(b"joined")

    download.STAGE.run(make_context(StageKey.DOWNLOAD))

    assert ytdlp.calls == []
    assert fake_ffmpeg.calls == []
    assert layout.full_video.read_bytes() == b"joined"


def test_a_download_without_parts_fails(make_context: MakeContext):
    with pytest.raises(SourceError, match="without video parts"):
        download.STAGE.run(make_context(StageKey.DOWNLOAD, ytdlp=FakeYtDlp()))
