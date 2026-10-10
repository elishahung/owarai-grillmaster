from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from tests.fakes import FAKE_JPEG

from grillmaster.media.errors import MediaError
from grillmaster.media.ffmpeg import SubprocessFfmpegRunner
from grillmaster.media.probe import duration
from grillmaster.media.video import concat_copy, concat_list_text, cut, join_parts

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tests.fakes import FakeFfmpeg


def make_parts(directory: Path, count: int) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    parts = [directory / f"{index}.mp4" for index in range(count)]
    for part in parts:
        part.write_bytes(part.name.encode())
    return parts


def option(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def test_a_single_part_is_moved_into_place(tmp_path: Path, fake_ffmpeg: FakeFfmpeg):
    (part,) = make_parts(tmp_path / "parts", 1)
    output = tmp_path / "video.mp4"

    join_parts(fake_ffmpeg, [part], output)

    assert fake_ffmpeg.calls == []
    assert output.read_bytes() == b"0.mp4"
    assert not part.exists()


def test_several_parts_are_concatenated_and_consumed(
    tmp_path: Path, fake_ffmpeg: FakeFfmpeg
):
    parts = make_parts(tmp_path / "parts", 2)
    output = tmp_path / "out" / "video.mp4"

    join_parts(fake_ffmpeg, parts, output)

    (argv,) = fake_ffmpeg.calls
    assert option(argv, "-f") == "concat"
    assert option(argv, "-safe") == "0"
    assert option(argv, "-c") == "copy"
    assert option(argv, "-map") == "0"
    assert option(argv, "-movflags") == "faststart"
    # Written under a temporary name with the muxer named, then renamed.
    assert argv[-3:] == ["-f", "mp4", str(output.with_name(".video.mp4.partial"))]
    assert output.read_bytes() == FAKE_JPEG
    assert not any(part.exists() for part in parts)
    assert [path.name for path in output.parent.iterdir()] == ["video.mp4"]


class TruncatingFfmpeg:
    """Writes half an output, then dies like an interrupted ffmpeg."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, argv: Sequence[str], **_kwargs: object) -> str:
        self.calls.append(list(argv))
        Path(argv[-1]).write_bytes(b"trunc")
        raise MediaError("ffmpeg was killed")


def test_an_interrupted_join_leaves_no_output_and_keeps_the_parts(tmp_path: Path):
    parts = make_parts(tmp_path / "parts", 2)
    output = tmp_path / "full.mp4"

    with pytest.raises(MediaError):
        join_parts(TruncatingFfmpeg(), parts, output)

    assert not output.exists()
    assert list(tmp_path.glob(".*partial")) == []
    assert all(part.exists() for part in parts)


def test_an_interrupted_cut_keeps_the_previous_output(tmp_path: Path):
    output = tmp_path / "video.mp4"
    output.write_bytes(b"previous")

    with pytest.raises(MediaError):
        cut(TruncatingFfmpeg(), tmp_path / "full.mp4", output, start=1.0)

    assert output.read_bytes() == b"previous"
    assert list(tmp_path.glob(".*partial")) == []


def test_failed_concat_keeps_the_parts(tmp_path: Path, fake_ffmpeg: FakeFfmpeg):
    parts = make_parts(tmp_path / "parts", 2)
    fake_ffmpeg.fail_with = RuntimeError("ffmpeg failed")

    with pytest.raises(RuntimeError):
        join_parts(fake_ffmpeg, parts, tmp_path / "video.mp4")

    assert all(part.exists() for part in parts)


def test_combining_nothing_fails(tmp_path: Path, fake_ffmpeg: FakeFfmpeg):
    with pytest.raises(ValueError, match="No video parts"):
        join_parts(fake_ffmpeg, [], tmp_path / "video.mp4")


def test_concat_list_uses_forward_slashes_and_escapes_quotes():
    text = concat_list_text([Path("C:/shows/0.mp4"), Path("C:/it's/1.mp4")])

    assert text == "file 'C:/shows/0.mp4'\nfile 'C:/it'\\''s/1.mp4'\n"


@pytest.mark.parametrize(
    ("bounds", "seek", "length"),
    [
        ((90.0, 600.0), "90.0", "510.0"),
        ((90.0, None), "90.0", None),
        ((None, 600.0), None, "600.0"),
    ],
)
def test_cut_seeks_before_the_input_and_limits_the_output(
    tmp_path: Path,
    fake_ffmpeg: FakeFfmpeg,
    bounds: tuple[float | None, float | None],
    seek: str | None,
    length: str | None,
):
    start, end = bounds
    source, output = tmp_path / "full.mp4", tmp_path / "video.mp4"

    cut(fake_ffmpeg, source, output, start=start, end=end)

    (argv,) = fake_ffmpeg.calls
    input_at = argv.index("-i")
    assert argv[input_at + 1] == str(source)
    assert (option(argv, "-ss") if "-ss" in argv else None) == seek
    if seek is not None:
        assert argv.index("-ss") < input_at
    assert (option(argv, "-t") if "-t" in argv else None) == length
    assert option(argv, "-c") == "copy"
    assert option(argv, "-avoid_negative_ts") == "make_zero"
    assert argv[-1] == str(output.with_name(".video.mp4.partial"))
    assert output.read_bytes() == FAKE_JPEG


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (None, None, "start or an end"),
        (60.0, 60.0, "not after"),
        (None, 0.0, "not after"),
    ],
)
def test_cut_rejects_an_empty_range(
    tmp_path: Path,
    fake_ffmpeg: FakeFfmpeg,
    start: float | None,
    end: float | None,
    message: str,
):
    with pytest.raises(ValueError, match=message):
        cut(fake_ffmpeg, tmp_path / "a.mp4", tmp_path / "b.mp4", start=start, end=end)
    assert fake_ffmpeg.calls == []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_combine_and_cut_with_real_ffmpeg(tmp_path: Path, media_fixture: Path):
    runner = SubprocessFfmpegRunner()
    parts = [tmp_path / "parts" / f"{index}.mp4" for index in range(2)]
    parts[0].parent.mkdir()
    for part in parts:
        shutil.copyfile(media_fixture, part)
    full, video = tmp_path / "full.mp4", tmp_path / "video.mp4"

    join_parts(runner, parts, full)
    cut(runner, full, video, start=1.0, end=3.0)

    assert duration(runner, full) == pytest.approx(4.0, abs=0.2)
    # Stream copy snaps to keyframes, so the cut may run a little long.
    assert 1.5 <= duration(runner, video) <= 3.5


class ListReadingFfmpeg:
    """Records each argv with the concat list's text, read while it exists."""

    def __init__(self) -> None:
        self.runs: list[tuple[list[str], str, dict[str, object]]] = []

    def run(self, argv: Sequence[str], **options: object) -> str:
        listing = Path(option(list(argv), "-i")).read_text(encoding="utf-8")
        self.runs.append((list(argv), listing, options))
        return ""


def test_concat_copy_puts_extra_args_between_the_list_and_the_output(tmp_path: Path):
    inputs = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    output = tmp_path / "out.mp4"
    runner = ListReadingFfmpeg()

    concat_copy(runner, inputs, output, "-i", "audio.m4a", "-c", "copy")

    ((argv, listing, options),) = runner.runs
    concat_at = argv.index("concat")
    assert argv[concat_at - 1 : concat_at + 4] == ["-f", "concat", "-safe", "0", "-i"]
    assert argv[concat_at + 5 :] == ["-i", "audio.m4a", "-c", "copy", str(output)]
    assert listing == concat_list_text(inputs)
    assert not Path(argv[concat_at + 4]).exists()
    assert options["timeout"] is None


def test_concat_copy_passes_progress_and_abort_to_the_runner(tmp_path: Path):
    runner = ListReadingFfmpeg()
    abort = threading.Event()

    def on_progress(_seconds: float) -> None:
        pass

    concat_copy(
        runner,
        [tmp_path / "a.mp4"],
        tmp_path / "out.mp4",
        on_progress=on_progress,
        abort=abort,
    )

    ((_, _, options),) = runner.runs
    assert options["on_progress"] is on_progress
    assert options["abort"] is abort


def test_concat_copy_needs_an_input(tmp_path: Path):
    with pytest.raises(ValueError, match="at least one input"):
        concat_copy(ListReadingFfmpeg(), [], tmp_path / "out.mp4")
