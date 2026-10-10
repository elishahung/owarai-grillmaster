"""Speech audio: extraction for ASR and translation, and gap detection.

Every extract encodes the same speech target (Opus in Ogg, mono, 16 kHz,
24 kbit/s) and is written under a dot-prefixed temporary name first, so an
interrupted run never leaves a truncated file behind under the final name.
"""

from __future__ import annotations

from itertools import pairwise
from typing import TYPE_CHECKING

from grillmaster.core.fs import partial_path
from grillmaster.core.timecode import TimeRange
from grillmaster.media.ffmpeg import ffmpeg, ffprobe

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.media.ffmpeg import FfmpegRunner

# Audio packets are tens of milliseconds apart; a larger jump means missing
# media (e.g. a skipped download fragment) that decoders silently collapse.
AUDIO_GAP_TOLERANCE_S = 1.0
_GAP_PROBE_TIMEOUT_S = 600.0
_SEGMENT_TIMEOUT_S = 300.0

# The codec is explicit because ffmpeg defaults `.ogg` to Vorbis; Opus keeps
# speech quality at this bitrate. Video is dropped explicitly because the Ogg
# muxer accepts video and would otherwise try (and fail) to encode Theora.
_SPEECH_ENCODE = (
    "-vn",
    "-c:a",
    "libopus",
    "-ac",
    "1",
    "-ar",
    "16000",
    "-b:a",
    "24k",
    "-f",
    "ogg",
)


def extract_audio(runner: FfmpegRunner, video: Path, output: Path) -> None:
    """Encode the whole audio track of `video` to `output` (Ogg/Opus)."""
    _encode(runner, ("-i", str(video)), output, timeout=None)


def extract_audio_segment(
    runner: FfmpegRunner, source: Path, output: Path, span: TimeRange
) -> None:
    """Encode `span` of `source` to `output` with the same speech settings.

    Always encodes; callers that cache slices check for `output` first.
    """
    if span.duration <= 0:
        raise ValueError(f"Audio segment duration must be positive: {span}")
    _encode(
        runner,
        ("-ss", f"{span.start:.3f}", "-t", f"{span.duration:.3f}", "-i", str(source)),
        output,
        timeout=_SEGMENT_TIMEOUT_S,
    )


def find_audio_gaps(runner: FfmpegRunner, path: Path) -> list[TimeRange]:
    """Spans where the first audio stream's packet timestamps jump.

    The container keeps the gap on its timeline, but decoding joins the
    audio across it, so anything timed from the decoded audio (ASR) drifts
    from the video by the gap length after that point. A file without audio
    yields no gaps.
    """
    output = runner.run(
        ffprobe(
            "-select_streams",
            "a:0",
            "-show_entries",
            "packet=pts_time",
            "-of",
            "csv=p=0",
            str(path),
        ),
        timeout=_GAP_PROBE_TIMEOUT_S,
    )
    pts = [
        float(field)
        for line in output.splitlines()
        if (field := line.strip().rstrip(",")) and field != "N/A"
    ]
    return [
        TimeRange(previous, current)
        for previous, current in pairwise(pts)
        if current - previous > AUDIO_GAP_TOLERANCE_S
    ]


def _encode(
    runner: FfmpegRunner,
    input_args: tuple[str, ...],
    output: Path,
    *,
    timeout: float | None,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = partial_path(output)
    try:
        runner.run(ffmpeg(*input_args, *_SPEECH_ENCODE, str(partial)), timeout=timeout)
        partial.replace(output)
    finally:
        partial.unlink(missing_ok=True)
