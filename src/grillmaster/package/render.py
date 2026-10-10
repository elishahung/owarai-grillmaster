"""The package look and the burned-in render (moved verbatim from legacy media).

Every package render applies one look: scale, a 0.2 degree rotate, crop,
grade and grain, then the burn plan's subtitle layers, then trim and the
1.03 tempo, encoded with NVENC; the audio gets the same tempo through
rubberband plus a -54 dB pink-noise bed. `burn_in` renders a whole show,
`render_subtitled_range` any set of parts of it (remix uses one part per
segment).

Do not "clean up" the filter strings or encode arguments: every value is
measured (see the comments on `_PACKAGE_VIDEO_FILTER` and the constants).
Throughput comes from running `PACKAGE_ENCODE_CONCURRENCY` NVENC sessions
side by side, not from GPU filters.
"""

from __future__ import annotations

import contextvars
import itertools
import shutil
import tempfile
import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from math import radians
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.timecode import TimeRange
from grillmaster.events.types import ProgressAdvanced, ProgressFinished, ProgressStarted
from grillmaster.media import probe
from grillmaster.media.ffmpeg import ffmpeg
from grillmaster.media.video import concat_list_text
from grillmaster.package.errors import PackageError, RenderAbortedError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from grillmaster.events.bus import EventSink
    from grillmaster.live_chat.render import PictureBox
    from grillmaster.media.ffmpeg import FfmpegRunner

BURN_IN_DURATION_TOLERANCE_SECONDS = 2.0
PACKAGE_TEMPO = 1.03
PACKAGE_PITCH = 1.01
PACKAGE_NOISE_AMPLITUDE = 0.002  # ≈ -54 dBFS
PACKAGE_LEAD_TRIM_SECONDS = 3
PACKAGE_SEEK_MARGIN_SECONDS = 2.0
PACKAGE_ROTATE_DEGREES = 0.2
PACKAGE_ROTATE_RADIANS = radians(PACKAGE_ROTATE_DEGREES)
PACKAGE_OUTPUT_FPS = 29.94
PACKAGE_FRAME_WIDTH = 1920
PACKAGE_FRAME_HEIGHT = 1080
# One NVENC session tops out well below what the card can do: three encodes
# running side by side finish in barely more wall time than one. Renders are
# therefore spread over this many concurrent ffmpeg processes — remix
# segments across the pool, and a plain burn-in split into this many parts.
PACKAGE_ENCODE_CONCURRENCY = 3
# Below this, splitting a burn-in costs more in seeks and muxing than the
# parallel encode wins back.
PACKAGE_MIN_PART_SECONDS = 120.0
# NVENC runs in quality-targeted VBR, so -cq alone decides the bitrate and
# -maxrate/-bufsize only cap the peaks. A -b:v next to -cq is inert: it read
# "6000k" here while the encoder shipped ~17 Mbps, so the target is stated as
# the quality level it actually is.
PACKAGE_VIDEO_CQ = "21"
PACKAGE_VIDEO_MAXRATE = "24000k"  # Bilibili 1080p recommended peak
PACKAGE_VIDEO_BUFSIZE = "12000k"  # required for -maxrate

# ``bilinear=0`` puts the rotate on nearest-neighbour interpolation. It is
# the one knob that matters here: rotate costs about two thirds of this
# chain, and dropping the interpolation makes a whole segment render ~1.6x
# faster. What it buys that speed with is a sub-pixel snap, and the snap is
# only visible on hard edges already in the picture (on-screen captions) —
# burned ASS is applied after rotate, so it stays upright and unsnapped.
# Measured against a real episode, flat areas came out identical, temporal
# jitter rose under 1%, and the ``noise`` grain below covers the rest.
# Delete ``:bilinear=0`` to go back to the smoother, slower default; nothing
# else depends on it.

# The grain flags are tuned for bitrate, not for looks. Temporal grain is
# what covers the snap above, and it is expensive: every frame differs, so
# inter prediction cannot reuse it. Measured on a real episode against the
# same chain with no grain, the old ``alls=3:allf=t`` cost +29% bitrate;
# ``c0s=4:c0f=t+u`` costs +17% for the same luma grain (0.18 dB apart).
# Two independent savings: chroma grain (``alls`` -> ``c0s``) codes badly
# and does no perceptual work, and the default distribution's long tail is
# what the encoder actually pays for, so ``u`` bounds it.
# Do not "save" further with the ``a`` flag or a lower strength. Both
# measure a frame-to-frame delta indistinguishable from no grain at all:
# they still look like grain in a still frame while having switched off
# the temporal masking this filter exists for.
_PACKAGE_VIDEO_FILTER = (
    "scale=1960:1102:flags=bicubic,"
    f"rotate=a={PACKAGE_ROTATE_RADIANS}:"
    f"ow=rotw({PACKAGE_ROTATE_RADIANS}):"
    f"oh=roth({PACKAGE_ROTATE_RADIANS}):c=black:bilinear=0,"
    f"crop={PACKAGE_FRAME_WIDTH}:{PACKAGE_FRAME_HEIGHT},"
    "eq=brightness=0.02:contrast=1.03:saturation=1.05,"
    "hue=h=4,"
    "noise=c0s=4:c0f=t+u"
)
_PACKAGE_VIDEO_OUTPUT = (
    f"setpts=PTS/{PACKAGE_TEMPO},format=yuv420p,fps={PACKAGE_OUTPUT_FPS}"
)
_PACKAGE_AUDIO_FILTER = (
    "highpass=f=50,"
    "lowpass=f=15000,"
    f"rubberband=tempo={PACKAGE_TEMPO}:pitch={PACKAGE_PITCH},"
    "aformat=sample_rates=44100:channel_layouts=stereo,"
    "volume=0.97"
)
PACKAGE_ENCODE_ARGS = (
    "-c:v",
    "h264_nvenc",
    "-preset",
    "p4",
    "-tune",
    "hq",
    "-rc",
    "vbr",
    "-cq",
    PACKAGE_VIDEO_CQ,
    "-maxrate",
    PACKAGE_VIDEO_MAXRATE,
    "-bufsize",
    PACKAGE_VIDEO_BUFSIZE,
    "-profile:v",
    "high",
    "-spatial-aq",
    "1",
    "-c:a",
    "aac",
    "-b:a",
    "192k",
    "-ar",
    "44100",
    "-ac",
    "2",
    "-movflags",
    "+faststart",
)
_PACKAGE_AUDIO_ENCODE_ARGS = ("-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2")


def package_usable_duration(source_duration: float) -> float:
    """Source length left after dropping the package lead-in."""
    usable = source_duration - PACKAGE_LEAD_TRIM_SECONDS
    if usable <= 0:
        raise PackageError(
            f"video is shorter than the {PACKAGE_LEAD_TRIM_SECONDS}s package lead trim"
        )
    return usable


def package_output_duration(source_duration: float) -> float:
    """Expected output length after the shared package tempo."""
    return source_duration / PACKAGE_TEMPO


# --- burn plan ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SubtitleLayer:
    """One ASS file to burn, optionally with libass style overrides.

    `force_style` is the `subtitles` filter's, e.g. `MarginR=394,MarginV=24`.
    """

    path: Path
    force_style: str | None = None

    def filter(self, cwd: Path) -> str:
        """The `subtitles` filter naming the file relative to `cwd`.

        Renders run with `cwd` at the video's directory: ffmpeg's filter
        syntax cannot take an absolute Windows path (the drive colon collides
        with the argument separator), and a relative one sidesteps the
        escaping entirely.
        """
        name = self.path.relative_to(cwd).as_posix()
        if self.force_style is None:
            return f"subtitles={name}"
        return f"subtitles={name}:force_style='{self.force_style}'"


@dataclass(frozen=True, slots=True)
class BurnPlan:
    """What a package render draws on top of the look.

    By default the graded frame fills the package frame. With `picture` it
    is shrunk into that box on a black canvas of the package frame size, so
    the output still concats with full-frame segments. `layers` then burn in
    order — later layers draw on top.
    """

    layers: tuple[SubtitleLayer, ...]
    picture: PictureBox | None = None

    def __post_init__(self) -> None:
        if not self.layers:
            raise ValueError("a burn plan needs at least one subtitle layer")
        box = self.picture
        if box is None:
            return
        if not (
            box.x >= 0
            and box.y >= 0
            and box.x + box.width <= PACKAGE_FRAME_WIDTH
            and box.y + box.height <= PACKAGE_FRAME_HEIGHT
        ):
            raise ValueError(f"picture box leaves the package frame: {box}")
        # 4:2:0 chroma covers 2x2 pixels: odd sizes or offsets get rounded.
        if any(value % 2 for value in (box.x, box.y, box.width, box.height)):
            raise ValueError(f"picture box must use even values: {box}")

    @classmethod
    def dialogue(cls, path: Path) -> BurnPlan:
        """Only the dialogue subtitles, on the full frame."""
        return cls(layers=(SubtitleLayer(path),))

    @property
    def canvas_filter(self) -> str | None:
        """Filters between the look and the subtitles, if any."""
        box = self.picture
        if box is None:
            return None
        return (
            f"scale={box.width}:{box.height},"
            f"pad={PACKAGE_FRAME_WIDTH}:{PACKAGE_FRAME_HEIGHT}:"
            f"{box.x}:{box.y}:black"
        )


def package_video_chain(burn: BurnPlan, trim_filter: str, cwd: Path) -> str:
    """Look, canvas, then ASS, then trim/tempo — one encode, upright text.

    The canvas filter sits after the look so letterbox padding gets no grade
    or grain. `subtitles` stays on the source timeline. `trim` and
    `setpts=PTS/tempo` come after burn-in so lip-sync still tracks the
    sped-up output. A second encode is not needed.
    """
    canvas = burn.canvas_filter
    return (
        f"{_PACKAGE_VIDEO_FILTER},"
        + (f"{canvas}," if canvas else "")
        + "".join(f"{layer.filter(cwd)}," for layer in burn.layers)
        + f"{trim_filter},"
        f"setpts=PTS-STARTPTS,"
        f"{_PACKAGE_VIDEO_OUTPUT}"
    )


def noise_bed_mix(duration_seconds: float) -> str:
    """Mix a -54 dB pink-noise bed under the labeled `[a0]` program.

    Pink rather than white: the program is low-passed at 15 kHz, so a
    flat-spectrum bed sits unmasked above that ceiling and reads as hiss in
    quiet passages.
    """
    return (
        f"anoisesrc=d={duration_seconds:.3f}:s=44100:"
        f"a={PACKAGE_NOISE_AMPLITUDE}:c=pink,"
        "aformat=sample_rates=44100:channel_layouts=stereo[an];"
        "[a0][an]amix=inputs=2:duration=first:"
        "dropout_transition=0:normalize=0[a]"
    )


def package_audio_graph(start_seconds: float, duration: float) -> str:
    """Audio-only graph: trim, package filter, then the noise bed."""
    noise_bed = noise_bed_mix(package_output_duration(duration))
    return (
        f"[0:a]atrim=start={start_seconds:.3f}:duration={duration:.3f},"
        f"asetpts=PTS-STARTPTS,{_PACKAGE_AUDIO_FILTER}[a0];"
        f"{noise_bed}"
    )


def package_seek_args(start_seconds: float, duration: float) -> list[str]:
    """Demuxer seek landing on the keyframe before `start_seconds`."""
    seek_seconds = max(0.0, start_seconds - PACKAGE_SEEK_MARGIN_SECONDS)
    read_seconds = start_seconds - seek_seconds + duration + PACKAGE_SEEK_MARGIN_SECONDS
    return [
        "-copyts",
        "-start_at_zero",
        "-ss",
        f"{seek_seconds:.3f}",
        "-t",
        f"{read_seconds:.3f}",
    ]


# --- progress ----------------------------------------------------------------


class RenderProgress:
    """One progress bar fed by several ffmpeg processes; thread-safe.

    Each process gets its own `track(duration)` callback, which turns
    ffmpeg's absolute output time into advances on the shared bar.
    """

    def __init__(self, events: EventSink, scope: str) -> None:
        self._events = events
        self.scope = scope
        self._lock = threading.Lock()

    def advance(self, seconds: float, note: str | None) -> None:
        if seconds > 0:
            with self._lock:
                self._events.emit(ProgressAdvanced(self.scope, seconds, note))

    def track(self, duration: float, note: str | None = None) -> ProcessProgress:
        return ProcessProgress(self, duration, note)


class ProcessProgress:
    """Progress of one ffmpeg process expected to output `duration` seconds."""

    def __init__(self, bar: RenderProgress, duration: float, note: str | None) -> None:
        self._bar = bar
        self._duration = duration
        self._note = note
        self._done = 0.0

    def __call__(self, seconds: float) -> None:
        current = min(seconds, self._duration)
        self._bar.advance(current - self._done, self._note)
        self._done = max(self._done, current)

    def complete(self) -> None:
        """Advance whatever the process did not report (it succeeded)."""
        self._bar.advance(self._duration - self._done, self._note)
        self._done = self._duration


@contextmanager
def render_progress(
    events: EventSink, scope: str, label: str, total: float
) -> Iterator[RenderProgress]:
    """A bar `scope` of `total` seconds, finished however the body ends."""
    events.emit(ProgressStarted(scope, label, total))
    try:
        yield RenderProgress(events, scope)
    finally:
        events.emit(ProgressFinished(scope))


def run_tracked(
    runner: FfmpegRunner,
    argv: Sequence[str],
    progress: ProcessProgress | None,
    *,
    abort: threading.Event | None,
    cwd: Path | None = None,
) -> None:
    """Run one package ffmpeg process, killed when `abort` is set.

    No timeout (renders take hours), but every run reports progress, so the
    runner's stall watchdog covers the untracked ones (audio, mux, concat)
    too.
    """
    runner.run(
        argv,
        cwd=cwd,
        on_progress=progress if progress is not None else _ignore_progress,
        abort=abort,
    )
    if progress is not None:
        progress.complete()


def _ignore_progress(_seconds: float) -> None:
    pass


def run_in_pool[T](
    jobs: Sequence[Callable[[threading.Event], T]],
    *,
    max_workers: int,
    abort: threading.Event | None = None,
) -> list[T]:
    """Run `jobs` concurrently in the caller's log scope; results in job order.

    Each job receives the pool's abort event (`abort`, or a fresh one) and
    hands it to its ffmpeg runs. The first failure (or the caller being
    interrupted) sets it, which kills the running ffmpeg processes; jobs
    not yet started are cancelled, or raise `RenderAbortedError` when a
    worker reaches them first. Once every job has ended, the first failure
    that is not such a skip is re-raised. A nested pool shares its caller's
    event, so a failure anywhere stops the whole render.
    """
    event = abort if abort is not None else threading.Event()

    def guarded(job: Callable[[threading.Event], T]) -> T:
        if event.is_set():
            raise RenderAbortedError("skipped: another render job failed")
        try:
            return job(event)
        except BaseException:
            # Set here, before this worker can pick up a queued job.
            event.set()
            raise

    def stop(futures: Sequence[Future[T]]) -> None:
        event.set()
        for future in futures:
            future.cancel()

    failures: list[BaseException] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(contextvars.copy_context().run, guarded, job) for job in jobs
        ]
        try:
            for future in as_completed(futures):
                error = None if future.cancelled() else future.exception()
                if error is not None:
                    failures.append(error)
                    stop(futures)
        except BaseException:
            stop(futures)
            raise
    if failures:
        raise next(
            (error for error in failures if not isinstance(error, RenderAbortedError)),
            failures[0],
        )
    return [future.result() for future in futures]


def check_output_duration(runner: FfmpegRunner, output: Path, expected: float) -> None:
    """Fail unless `output` lasts `expected` seconds, within the tolerance.

    ffmpeg can exit 0 on a truncated file (an SMB share dropping behind a VPN
    switch), and a same-length output is a failed speed-up, so every render
    is probed rather than trusted.
    """
    actual = probe.duration(runner, output)
    error = abs(expected - actual)
    if error > BURN_IN_DURATION_TOLERANCE_SECONDS:
        raise PackageError(
            f"render output duration differs from expected by {error:.3f}s: "
            f"{output} ({actual:.3f}s vs {expected:.3f}s)"
        )


# --- burn-in -----------------------------------------------------------------


def burn_in(
    runner: FfmpegRunner,
    video: Path,
    burn: BurnPlan,
    output: Path,
    *,
    events: EventSink,
) -> None:
    """Apply the package look, then burn the plan's ASS so rotate cannot tilt text.

    The render is split into video parts that encode side by side plus one
    pass over the audio (see `render_subtitled_range`). The audio is never
    split: a seam between two separately rubberband-stretched halves is
    audible, while a seam between two independently encoded video parts is
    not. The output's duration is checked against the sped-up expectation:
    a same-length output is a failed speed-up, not a success.

    The burn plan's layers must live under the video's directory (see
    `SubtitleLayer.filter`).
    """
    check_subtitles_under(video, burn)
    output.parent.mkdir(parents=True, exist_ok=True)
    logger.info(
        f"Burning subtitles {', '.join(layer.path.name for layer in burn.layers)} "
        f"into {video.name} -> {output}"
    )
    usable_duration = package_usable_duration(probe.duration(runner, video))
    expected = package_output_duration(usable_duration)
    parts = burn_in_parts(usable_duration)
    logger.info(f"Burn-in rendering {len(parts)} video part(s)")
    with render_progress(
        events, "package:burn-in", "Burning subtitles", expected
    ) as progress:
        render_subtitled_range(
            runner,
            video=video,
            burn=burn,
            output=output,
            parts=parts,
            progress=progress,
        )
    check_output_duration(runner, output, expected)


def burn_in_parts(usable_duration: float) -> list[TimeRange]:
    """Split the burn-in range into parts that encode in parallel.

    Boundaries land on whole output frames so the parts concatenate to the
    frame count a single pass would have produced: a source offset maps to
    output time as `offset / PACKAGE_TEMPO`, so one output frame is
    `PACKAGE_TEMPO / PACKAGE_OUTPUT_FPS` of source.
    """
    start = float(PACKAGE_LEAD_TRIM_SECONDS)
    end = start + usable_duration
    part_count = min(
        PACKAGE_ENCODE_CONCURRENCY,
        max(1, int(usable_duration // PACKAGE_MIN_PART_SECONDS)),
    )
    if part_count == 1:
        return [TimeRange(start, end)]

    frame_count = int(package_output_duration(usable_duration) * PACKAGE_OUTPUT_FPS)
    source_per_frame = PACKAGE_TEMPO / PACKAGE_OUTPUT_FPS
    boundaries = [start]
    boundaries += [
        start + round(index * frame_count / part_count) * source_per_frame
        for index in range(1, part_count)
    ]
    boundaries.append(end)
    return [TimeRange(lower, upper) for lower, upper in itertools.pairwise(boundaries)]


def render_subtitled_range(
    runner: FfmpegRunner,
    *,
    video: Path,
    burn: BurnPlan,
    output: Path,
    parts: Sequence[TimeRange],
    progress: RenderProgress,
    note: str | None = None,
    abort: threading.Event | None = None,
) -> None:
    """Render `parts` as video and the whole span as audio, then mux.

    Every ffmpeg process here owns exactly one filtergraph. That is not a
    style choice: a single process feeding both a `-vf` graph and an audio
    `-filter_complex` from the same input deadlocks partway through a long
    range, and putting both in one `-filter_complex` runs them in series
    instead (one graph, one thread). Separate processes give the
    parallelism without either failure mode.

    Only the video parts report progress; together they cover the whole
    output, so the bar's total still adds up. `abort` is the caller's pool
    event (see `run_in_pool`) when this range is one job of a larger render.
    """
    check_subtitles_under(video, burn)
    abort = abort if abort is not None else threading.Event()
    temp_dir = Path(tempfile.mkdtemp(prefix="grill_render_"))
    try:
        part_files = [temp_dir / f"part{index:03d}.mp4" for index in range(len(parts))]
        audio_file = temp_dir / "audio.m4a"
        jobs: list[Callable[[threading.Event], None]] = [
            lambda event, part=part, part_file=part_file: _encode_subtitled_range(
                runner,
                video=video,
                burn=burn,
                output=part_file,
                part=part,
                progress=progress.track(package_output_duration(part.duration), note),
                abort=event,
            )
            for part, part_file in zip(parts, part_files, strict=True)
        ]
        jobs.append(
            lambda event: _encode_package_audio(
                runner,
                video=video,
                output=audio_file,
                span=TimeRange(parts[0].start, parts[-1].end),
                abort=event,
            )
        )
        run_in_pool(jobs, max_workers=len(jobs), abort=abort)
        _mux_package_output(
            runner,
            part_files=part_files,
            audio_file=audio_file,
            output=output,
            scratch=temp_dir,
            abort=abort,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _encode_subtitled_range(
    runner: FfmpegRunner,
    *,
    video: Path,
    burn: BurnPlan,
    output: Path,
    part: TimeRange,
    progress: ProcessProgress,
    abort: threading.Event,
) -> None:
    """Burn subtitles into one trimmed, normalized range, video only.

    The range is reached with a demuxer seek rather than by decoding the
    whole prefix: without it every segment would decode — and rasterize
    subtitles over — the entire video up to its start. `-copyts` keeps the
    packets on the source timeline so the `subtitles` filter still picks the
    right lines and the absolute `trim`/`atrim` bounds below stay correct;
    `-start_at_zero` cancels a non-zero container start time, exactly as the
    default (non-`copyts`) path would. The seek lands on the keyframe at or
    before the margin, and `trim` still cuts the exact frame, so the output
    is unchanged.

    The matching audio comes from `_encode_package_audio` in its own process;
    see `render_subtitled_range` for why.

    The stream is picked as `0:v:0`, never `0:v`: sources carrying a
    cover-art mjpeg stream would otherwise get it re-encoded to h264 as a
    second video stream, which mp4 cannot tag as attached art.
    """
    duration = part.duration
    if duration <= 0:
        raise ValueError("segment duration must be positive")
    output.parent.mkdir(parents=True, exist_ok=True)
    cwd = video.parent
    argv = ffmpeg(
        *package_seek_args(part.start, duration),
        "-i",
        video.name,
        "-vf",
        package_video_chain(
            burn, f"trim=start={part.start:.3f}:duration={duration:.3f}", cwd
        ),
        "-map",
        "0:v:0",
        "-an",
        *PACKAGE_ENCODE_ARGS,
        str(output),
    )
    run_tracked(runner, argv, progress, abort=abort, cwd=cwd)


def _encode_package_audio(
    runner: FfmpegRunner,
    *,
    video: Path,
    output: Path,
    span: TimeRange,
    abort: threading.Event,
) -> None:
    """Render the package audio for one range as a standalone track."""
    duration = span.duration
    if duration <= 0:
        raise ValueError("audio duration must be positive")
    output.parent.mkdir(parents=True, exist_ok=True)
    argv = ffmpeg(
        *package_seek_args(span.start, duration),
        "-i",
        str(video),
        "-filter_complex",
        package_audio_graph(span.start, duration),
        "-map",
        "[a]",
        "-vn",
        *_PACKAGE_AUDIO_ENCODE_ARGS,
        str(output),
    )
    run_tracked(runner, argv, None, abort=abort)


def _mux_package_output(
    runner: FfmpegRunner,
    *,
    part_files: Sequence[Path],
    audio_file: Path,
    output: Path,
    scratch: Path,
    abort: threading.Event,
) -> None:
    """Concatenate the video parts and mux the one audio track in."""
    concat_list = scratch / "parts.txt"
    concat_list.write_text(concat_list_text(part_files), encoding="utf-8")
    argv = ffmpeg(
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-i",
        str(audio_file),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output),
    )
    run_tracked(runner, argv, None, abort=abort)


def check_subtitles_under(video: Path, burn: BurnPlan) -> None:
    """Refuse layers outside the video's directory (see `SubtitleLayer.filter`)."""
    for layer in burn.layers:
        if not layer.path.is_relative_to(video.parent):
            raise PackageError(
                f"subtitles must live under the video's directory for burn-in: "
                f"{layer.path} is not under {video.parent}"
            )
