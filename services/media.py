"""Media processing utilities for audio extraction, chunk slicing, and frames.

This module provides the MediaProcessor class for handling common media operations
such as extracting audio from video files, slicing chunk audio, sampling frames,
and combining multiple video files.
"""

from collections import deque
from math import radians
from pathlib import Path
import ffmpeg
import subprocess
import tempfile
import os
import threading
import shutil
from loguru import logger
from pydantic import BaseModel

from services.progress import NoopProgressReporter


BURN_IN_DURATION_TOLERANCE_SECONDS = 2.0
PACKAGE_TEMPO = 1.03
PACKAGE_PITCH = 1.01
PACKAGE_NOISE_AMPLITUDE = 0.008  # ≈ -42 dBFS
PACKAGE_LEAD_TRIM_SECONDS = 3
PACKAGE_SEEK_MARGIN_SECONDS = 2.0
PACKAGE_ROTATE_DEGREES = 0.2
PACKAGE_ROTATE_RADIANS = radians(PACKAGE_ROTATE_DEGREES)
PACKAGE_VIDEO_BITRATE = "6000k"  # Bilibili 1080p recommended average
PACKAGE_VIDEO_MAXRATE = "24000k"  # Bilibili 1080p recommended peak
PACKAGE_VIDEO_BUFSIZE = "12000k"  # 2× average; required for -maxrate


def package_usable_duration(source_duration: float) -> float:
    """Source length left after dropping the package lead-in."""
    usable = source_duration - PACKAGE_LEAD_TRIM_SECONDS
    if usable <= 0:
        raise ValueError(
            f"video is shorter than the {PACKAGE_LEAD_TRIM_SECONDS}s "
            "package lead trim"
        )
    return usable


def package_output_duration(source_duration: float) -> float:
    """Expected output length after the shared package tempo."""
    return source_duration / PACKAGE_TEMPO


class TimeRange(BaseModel):
    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)


class NoiseCut(BaseModel):
    """One slice of a noise source, transcoded on demand at remix time."""

    source: Path
    start_seconds: float
    duration_seconds: float


class MediaProcessor:
    """A utility class for processing media files using ffmpeg.

    This class provides static methods for common media processing tasks including
    audio extraction and video concatenation.
    """

    @staticmethod
    def extract_audio(input_file: Path, output_file: Path) -> Path:
        """Extract audio from a video file and encode it as Opus in Ogg.

        The audio is extracted with the following settings:
        - Drop video (vn)
        - Opus codec (libopus)
        - Mono channel (ac=1)
        - 16kHz sample rate (ar=16000)
        - 24k bitrate

        The codec is set explicitly because ffmpeg defaults the .ogg
        container to Vorbis; we keep Opus for speech quality at this bitrate.
        Video is dropped explicitly because the .ogg container (unlike the
        .opus muxer) accepts video, so ffmpeg would otherwise try to
        re-encode the source video stream to Theora and fail.

        Args:
            input_file: Path to the input video file.

        Returns:
            Path to the output audio file with .ogg extension.

        Raises:
            ffmpeg.Error: If the extraction process fails.
        """
        logger.info(f"Extracting audio from video: {input_file}")
        try:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            ffmpeg.input(str(input_file)).output(
                str(output_file),
                vn=None,
                acodec="libopus",
                ac=1,
                ar="16000",
                audio_bitrate="24k",
            ).run()
            logger.success(f"Successfully extracted audio to: {output_file}")
            return output_file
        except Exception as e:
            logger.error(f"Failed to extract audio from '{input_file}': {e}")
            raise

    @staticmethod
    def combine_videos(input_files: list[Path], output_file: Path) -> None:
        """Combine multiple video files into a single output file.

        If only one input file is provided, it will be renamed to the output file.
        If multiple files are provided, they are concatenated using ffmpeg's concat
        demuxer without re-encoding (using copy codec).

        Note: All input files are deleted after successful combination.

        Args:
            input_files: List of paths to input video files to be combined.
            output_file: Path where the combined video will be saved.

        Raises:
            AssertionError: If the input_files list is empty.
            ffmpeg.Error: If the video combination process fails.
        """
        logger.info(
            f"Combining {len(input_files)} video(s) into: {output_file}"
        )
        assert len(input_files) > 0, "No input files provided"

        try:
            if len(input_files) == 1:
                only_file = input_files[0]
                logger.debug(
                    f"Single input file, renaming {only_file} to {output_file}"
                )
                os.rename(only_file, output_file)
                logger.success(
                    f"Successfully created output file: {output_file}"
                )
                return

            logger.debug(
                f"Creating concat file list for {len(input_files)} videos"
            )
            concat_path = MediaProcessor._write_concat_list(
                sorted(input_files)
            )
            try:
                logger.debug("Concatenating videos using ffmpeg")
                ffmpeg.input(
                    str(concat_path), format="concat", safe=0
                ).output(
                    str(output_file),
                    c="copy",
                    map=0,
                    movflags="faststart",
                ).run(
                    overwrite_output=True
                )
            finally:
                concat_path.unlink(missing_ok=True)

            logger.debug("Cleaning up input files")
            for input_file in input_files:
                input_file.unlink()

            logger.success(f"Successfully combined videos into: {output_file}")
        except Exception as e:
            logger.error(f"Failed to combine videos: {e}")
            raise

    @staticmethod
    def cut_video(
        input_file: Path,
        output_file: Path,
        start_seconds: float | None = None,
        end_seconds: float | None = None,
    ) -> None:
        """Cut a section out of a video without re-encoding.

        Uses stream copy, so the actual cut points snap to the nearest
        keyframes; the output may include slightly more content than the
        requested range.

        Args:
            input_file: Path to the source video.
            output_file: Path where the cut video will be saved.
            start_seconds: Optional section start; defaults to the beginning.
            end_seconds: Optional section end; defaults to the end.

        Raises:
            ValueError: If no boundary is given or the range is empty.
            ffmpeg.Error: If the cut process fails.
        """
        if start_seconds is None and end_seconds is None:
            raise ValueError("cut_video requires start_seconds or end_seconds")
        if (
            end_seconds is not None
            and end_seconds <= (start_seconds or 0.0)
        ):
            raise ValueError("end_seconds must be later than start_seconds")

        input_kwargs: dict = {}
        if start_seconds is not None:
            input_kwargs["ss"] = start_seconds
        output_kwargs: dict = {
            "c": "copy",
            "avoid_negative_ts": "make_zero",
            "movflags": "faststart",
        }
        if end_seconds is not None:
            output_kwargs["t"] = end_seconds - (start_seconds or 0.0)

        logger.info(
            f"Cutting video section {start_seconds}-{end_seconds}s "
            f"from {input_file} to {output_file}"
        )
        try:
            ffmpeg.input(str(input_file), **input_kwargs).output(
                str(output_file), **output_kwargs
            ).run(overwrite_output=True)
            logger.success(f"Successfully cut video to: {output_file}")
        except Exception as e:
            logger.error(f"Failed to cut video '{input_file}': {e}")
            raise

    @staticmethod
    def burn_in_subtitles(
        video_file: Path,
        subtitle_file: Path,
        output_file: Path,
        progress: NoopProgressReporter | None = None,
    ) -> None:
        """Apply the package look, then burn ASS/SRT so rotate cannot tilt text.

        Implementation note: ffmpeg's ``subtitles`` filter does not handle
        absolute Windows paths reliably (colon parsing collides with filter
        argument syntax). This method runs ffmpeg with ``cwd`` set to the
        video's parent directory and references the subtitle by relative
        filename, which sidesteps the escaping problem entirely. The video
        and subtitle must therefore live in the same directory.

        Raises:
            ValueError: If video and subtitle are not in the same directory.
            subprocess.CalledProcessError: If ffmpeg exits non-zero.
        """
        cwd = video_file.parent
        if subtitle_file.parent != cwd:
            raise ValueError(
                f"video and subtitle must share a directory for burn-in: "
                f"{cwd} vs {subtitle_file.parent}"
            )

        output_file.parent.mkdir(parents=True, exist_ok=True)
        logger.info(
            f"Burning subtitles {subtitle_file.name} into "
            f"{video_file.name} -> {output_file}"
        )
        usable_duration = package_usable_duration(
            MediaProcessor.get_media_duration(video_file)
        )
        duration_seconds = package_output_duration(usable_duration)
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-nostats",
            "-progress",
            "pipe:1",
            "-i",
            video_file.name,
            "-filter_complex",
            (
                f"[0:v]{MediaProcessor._package_video_chain(
                    subtitle_file.name,
                    f'trim=start={PACKAGE_LEAD_TRIM_SECONDS}:duration={usable_duration:.3f}',
                )}[v];"
                f"[0:a]atrim=start={PACKAGE_LEAD_TRIM_SECONDS}:duration={usable_duration:.3f},"
                f"asetpts=PTS-STARTPTS,{MediaProcessor._PACKAGE_AUDIO_FILTER}[a0];"
                f"{MediaProcessor._white_noise_mix(duration_seconds)}"
            ),
            "-map",
            "[v]",
            "-map",
            "[a]",
            *MediaProcessor._PACKAGE_ENCODE_ARGS,
            str(output_file),
            "-y",
        ]
        progress_task = (
            progress.start_stage("Burning subtitles", total=duration_seconds)
            if progress is not None
            else None
        )
        try:
            MediaProcessor._run_ffmpeg_progress(
                cmd=cmd,
                cwd=cwd,
                progress=progress,
                progress_task=progress_task,
                duration_seconds=duration_seconds,
                progress_description=None,
                failure_label="burn-in",
            )
            output_duration = MediaProcessor.get_media_duration(output_file)
            duration_error = abs(duration_seconds - output_duration)
            if duration_error > BURN_IN_DURATION_TOLERANCE_SECONDS:
                raise ValueError(
                    f"burn-in output duration differs from expected by "
                    f"{duration_error:.3f}s: {output_file} "
                    f"({output_duration:.3f}s vs {duration_seconds:.3f}s)"
                )
        except Exception:
            if progress is not None:
                progress.finish(progress_task, "failed")
            raise

        if progress is not None:
            progress.finish(progress_task)

    @staticmethod
    def encode_noise_segment(
        cut: NoiseCut,
        output_file: Path,
        progress: NoopProgressReporter | None = None,
        progress_task=None,
        progress_description: str | None = None,
    ) -> None:
        """Transcode one noise slice into the package output format.

        Format-only fit (1920x1080 yuv420p @ 29.94, 44100 stereo, same
        encoder as the content segments) so remix concat can stream-copy the
        video. The look filters — rotate, grade, grain, tempo, rubberband,
        white-noise bed — are not applied.
        """
        if cut.duration_seconds <= 0:
            raise ValueError("noise cut duration must be positive")
        if not cut.source.exists():
            raise FileNotFoundError(f"noise source not found: {cut.source}")

        output_file.parent.mkdir(parents=True, exist_ok=True)
        logger.info(
            f"Encoding noise from {cut.source.name} at "
            f"{cut.start_seconds:.3f}s for {cut.duration_seconds:.3f}s"
        )
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-progress",
            "pipe:1",
            "-ss",
            f"{cut.start_seconds:.3f}",
            "-t",
            f"{cut.duration_seconds:.3f}",
            "-i",
            str(cut.source),
            "-filter_complex",
            (
                f"[0:v]{MediaProcessor._NOISE_VIDEO_FILTER}[v];"
                f"[0:a]{MediaProcessor._NOISE_AUDIO_FILTER}[a]"
            ),
            "-map",
            "[v]",
            "-map",
            "[a]",
            *MediaProcessor._PACKAGE_ENCODE_ARGS,
            str(output_file),
            "-y",
        ]
        MediaProcessor._run_ffmpeg_progress(
            cmd=cmd,
            progress=progress,
            progress_task=progress_task,
            duration_seconds=cut.duration_seconds,
            progress_description=progress_description,
            failure_label="noise segment",
        )

    @staticmethod
    def encode_subtitled_segment(
        video_file: Path,
        subtitle_file: Path,
        output_file: Path,
        start_seconds: float,
        end_seconds: float,
        progress: NoopProgressReporter | None = None,
        progress_task=None,
        progress_description: str | None = None,
    ) -> None:
        """Burn subtitles into a trimmed normalized segment.

        The segment is reached with a demuxer seek rather than by decoding the
        whole prefix: without it every segment would decode — and rasterize
        subtitles over — the entire video up to its start. ``-copyts`` keeps
        the packets on the source timeline so the ``subtitles`` filter still
        picks the right lines and the absolute ``trim``/``atrim`` bounds below
        stay correct; ``-start_at_zero`` cancels a non-zero container start
        time, exactly as the default (non-``copyts``) path would. The seek
        lands on the keyframe at or before the margin, and ``trim`` still cuts
        the exact frame, so the output is unchanged.
        """
        if subtitle_file.parent != video_file.parent:
            raise ValueError(
                f"video and subtitle must share a directory for segment "
                f"burn-in: {video_file.parent} vs {subtitle_file.parent}"
            )
        duration = max(0.0, end_seconds - start_seconds)
        if duration <= 0:
            raise ValueError("segment duration must be positive")
        output_duration = package_output_duration(duration)
        seek_seconds = max(0.0, start_seconds - PACKAGE_SEEK_MARGIN_SECONDS)
        read_seconds = (
            start_seconds - seek_seconds + duration + PACKAGE_SEEK_MARGIN_SECONDS
        )

        output_file.parent.mkdir(parents=True, exist_ok=True)
        filter_complex = (
            f"[0:v]{MediaProcessor._package_video_chain(
                subtitle_file.name,
                f'trim=start={start_seconds:.3f}:duration={duration:.3f}',
            )}[v];"
            f"[0:a]atrim=start={start_seconds:.3f}:duration={duration:.3f},"
            f"asetpts=PTS-STARTPTS,{MediaProcessor._PACKAGE_AUDIO_FILTER}[a0];"
            f"{MediaProcessor._white_noise_mix(output_duration)}"
        )
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-progress",
            "pipe:1",
            "-copyts",
            "-start_at_zero",
            "-ss",
            f"{seek_seconds:.3f}",
            "-t",
            f"{read_seconds:.3f}",
            "-i",
            video_file.name,
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            "[a]",
            *MediaProcessor._PACKAGE_ENCODE_ARGS,
            str(output_file),
            "-y",
        ]
        MediaProcessor._run_ffmpeg_progress(
            cmd=cmd,
            cwd=video_file.parent,
            progress=progress,
            progress_task=progress_task,
            duration_seconds=output_duration,
            progress_description=progress_description,
            failure_label="remix segment",
        )

    @staticmethod
    def _run_ffmpeg_progress(
        cmd: list[str],
        progress: NoopProgressReporter | None,
        progress_task,
        duration_seconds: float,
        progress_description: str | None,
        failure_label: str,
        cwd: Path | None = None,
    ) -> None:
        """Run ffmpeg and advance an existing task from progress output."""
        stderr_tail_lines: deque[str] = deque(maxlen=20)
        process = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        def collect_stderr() -> None:
            assert process.stderr is not None
            for line in process.stderr:
                stderr_tail_lines.append(line.rstrip())

        stderr_thread = threading.Thread(target=collect_stderr, daemon=True)
        stderr_thread.start()

        progress_seconds = 0.0
        assert process.stdout is not None
        for line in process.stdout:
            key, separator, value = line.strip().partition("=")
            if separator == "" or key not in {"out_time_ms", "out_time_us"}:
                continue
            try:
                current_seconds = int(value) / 1_000_000
            except ValueError:
                continue
            current_seconds = min(current_seconds, duration_seconds)
            delta_seconds = max(0.0, current_seconds - progress_seconds)
            if progress is not None:
                progress.advance(
                    progress_task,
                    delta_seconds,
                    description=progress_description,
                )
            progress_seconds = current_seconds

        return_code = process.wait()
        stderr_thread.join()
        if return_code == 0:
            if progress is not None:
                progress.advance(
                    progress_task,
                    max(0.0, duration_seconds - progress_seconds),
                    description=progress_description,
                )
            return

        stderr_tail = "\n".join(stderr_tail_lines)
        logger.error(
            f"ffmpeg {failure_label} failed (exit {return_code}): "
            f"{stderr_tail}"
        )
        raise subprocess.CalledProcessError(
            return_code,
            cmd,
            stderr=stderr_tail,
        )

    @staticmethod
    def concat_remix_segments(
        input_files: list[Path],
        output_file: Path,
        progress: NoopProgressReporter | None = None,
    ) -> None:
        """Concatenate normalized remix segments into an upload-safe MP4."""
        if not input_files:
            raise ValueError("input_files must not be empty")
        output_file.parent.mkdir(parents=True, exist_ok=True)
        concat_path = MediaProcessor._write_concat_list(input_files)
        try:
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_path),
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-af",
                "aresample=async=1:first_pts=0",
                "-avoid_negative_ts",
                "make_zero",
                "-movflags",
                "+faststart",
                str(output_file),
                "-y",
            ]
            progress_context = (
                progress.suspend()
                if progress is not None
                else NoopProgressReporter().suspend()
            )
            with progress_context:
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
            if result.returncode != 0:
                stderr_tail = "\n".join(result.stderr.splitlines()[-20:])
                logger.error(
                    f"ffmpeg remix concat failed "
                    f"(exit {result.returncode}): {stderr_tail}"
                )
                raise subprocess.CalledProcessError(
                    result.returncode,
                    cmd,
                    output=result.stdout,
                    stderr=result.stderr,
                )
        finally:
            concat_path.unlink(missing_ok=True)

    @staticmethod
    def build_remix_output(
        video_file: Path,
        subtitle_file: Path,
        output_file: Path,
        head_noise: NoiseCut,
        tail_noise: NoiseCut,
        start_seconds: float,
        end_seconds: float,
        progress: NoopProgressReporter | None = None,
        progress_task=None,
    ) -> None:
        """Create one noise + subtitled segment + noise remix output."""
        temp_dir = Path(tempfile.mkdtemp(prefix="grill_remix_"))
        try:
            head_segment = temp_dir / "head.mp4"
            target_segment = temp_dir / "target.mp4"
            tail_segment = temp_dir / "tail.mp4"
            MediaProcessor.encode_noise_segment(
                cut=head_noise,
                output_file=head_segment,
                progress=progress,
                progress_task=progress_task,
                progress_description=f"Noise for {output_file.name}",
            )
            MediaProcessor.encode_subtitled_segment(
                video_file=video_file,
                subtitle_file=subtitle_file,
                output_file=target_segment,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                progress=progress,
                progress_task=progress_task,
                progress_description=f"Remixing {output_file.name}",
            )
            MediaProcessor.encode_noise_segment(
                cut=tail_noise,
                output_file=tail_segment,
                progress=progress,
                progress_task=progress_task,
                progress_description=f"Noise for {output_file.name}",
            )
            MediaProcessor.concat_remix_segments(
                [head_segment, target_segment, tail_segment],
                output_file,
                progress=progress,
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    @staticmethod
    def parse_timecode_line(timecode: str) -> TimeRange:
        """Parse a single SRT timecode line into seconds."""
        start_str, end_str = [part.strip() for part in timecode.split("-->")]
        return TimeRange(
            start_seconds=MediaProcessor._parse_timestamp(start_str),
            end_seconds=MediaProcessor._parse_timestamp(end_str),
        )

    @staticmethod
    def get_media_duration(input_file: Path) -> float:
        """Read media duration in seconds from ffprobe."""
        probe = ffmpeg.probe(str(input_file))
        format_info = probe.get("format", {})
        duration = format_info.get("duration")
        if duration is None:
            raise ValueError(f"Media duration missing: {input_file}")
        return float(duration)

    @staticmethod
    def extract_audio_segment(
        input_file: Path,
        output_file: Path,
        start_seconds: float,
        end_seconds: float,
    ) -> Path:
        """Extract an audio slice with the same target settings as full audio."""
        duration = max(0.0, end_seconds - start_seconds)
        if duration <= 0:
            raise ValueError("Audio segment duration must be positive")

        if output_file.exists():
            logger.debug(f"Reusing cached audio segment: {output_file}")
            return output_file

        output_file.parent.mkdir(parents=True, exist_ok=True)
        logger.info(
            f"Extracting audio segment {start_seconds:.3f}-{end_seconds:.3f}s "
            f"to {output_file}"
        )
        try:
            (
                ffmpeg.input(str(input_file), ss=start_seconds, t=duration)
                .output(
                    str(output_file),
                    vn=None,
                    acodec="libopus",
                    ac=1,
                    ar="16000",
                    audio_bitrate="24k",
                )
                .run(
                    overwrite_output=True,
                    capture_stdout=True,
                    capture_stderr=True,
                )
            )
            return output_file
        except ffmpeg.Error as e:
            stderr = (
                e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
            )
            logger.error(
                f"Failed to extract audio segment "
                f"{start_seconds:.3f}-{end_seconds:.3f}s: {stderr}"
            )
            raise

    @staticmethod
    def extract_video_frame(
        input_file: Path,
        output_file: Path,
        timestamp_seconds: float,
        max_side: int,
    ) -> Path:
        """Extract a single JPEG frame with longest side constrained."""
        if max_side <= 0:
            raise ValueError("max_side must be positive")
        if output_file.exists():
            logger.debug(f"Reusing cached frame: {output_file}")
            return output_file

        output_file.parent.mkdir(parents=True, exist_ok=True)
        logger.info(
            f"Extracting frame at {timestamp_seconds:.3f}s to {output_file}"
        )
        scale_filter = (
            f"if(gte(iw,ih),{max_side},-2)",
            f"if(gte(iw,ih),-2,{max_side})",
        )
        try:
            stream = ffmpeg.input(str(input_file), ss=timestamp_seconds)
            (
                stream.filter("scale", *scale_filter)
                .output(
                    str(output_file),
                    vframes=1,
                    format="image2",
                    vcodec="mjpeg",
                    qscale=2,
                )
                .run(
                    overwrite_output=True,
                    capture_stdout=True,
                    capture_stderr=True,
                )
            )
            return output_file
        except ffmpeg.Error as e:
            stderr = (
                e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
            )
            logger.error(
                f"Failed to extract frame at {timestamp_seconds:.3f}s: {stderr}"
            )
            raise

    @staticmethod
    def extract_frames_at(
        input_file: Path,
        output_dir: Path,
        timestamps: list[float],
        max_side: int,
    ) -> list[Path]:
        """Extract JPEG frames at the given timestamps into ``output_dir``.

        Thin loop over ``extract_video_frame`` (caching, scaling, and encoding
        are inherited), reusing the ``frame_{ts:010.3f}_{max_side}.jpg`` naming
        convention shared with the pre-pass/chunk asset builders. Frames that
        fail to extract are skipped with a warning; the returned list preserves
        chronological order of the inputs.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for timestamp in timestamps:
            output_file = output_dir / f"frame_{timestamp:010.3f}_{max_side}.jpg"
            try:
                MediaProcessor.extract_video_frame(
                    input_file=input_file,
                    output_file=output_file,
                    timestamp_seconds=timestamp,
                    max_side=max_side,
                )
            except Exception as e:
                logger.warning(f"Skipping frame at {timestamp:.3f}s: {e}")
                continue
            paths.append(output_file)
        return paths

    @staticmethod
    def evenly_spaced_timestamps(
        duration_seconds: float, max_frames: int
    ) -> list[float]:
        """Return evenly spaced timestamps inside a media range."""
        if duration_seconds <= 0 or max_frames <= 0:
            return []
        frame_count = max_frames
        interval = duration_seconds / (frame_count + 1)
        return [interval * index for index in range(1, frame_count + 1)]

    @staticmethod
    def absolute_interval_timestamps(
        start_seconds: float,
        end_seconds: float,
        interval_seconds: float,
        include_start: bool,
        include_end: bool,
    ) -> list[float]:
        """Return deterministic absolute timestamps within a time range.

        When ``include_end`` is True, ``end_seconds`` is always added even if it
        does not align with the interval lattice.
        """
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        timestamps: set[float] = set()
        if include_start:
            timestamps.add(round(start_seconds, 3))

        first_slot = int(start_seconds // interval_seconds)
        current = first_slot * interval_seconds
        if current < start_seconds:
            current += interval_seconds

        while current < end_seconds:
            if start_seconds <= current:
                timestamps.add(round(current, 3))
            current += interval_seconds

        if include_end:
            timestamps.add(round(end_seconds, 3))

        return sorted(timestamps)

    @staticmethod
    def _write_concat_list(input_files: list[Path]) -> Path:
        """Write a concat demuxer script and return its path.

        Paths are normalized to forward slashes because the concat demuxer
        treats a backslash as an escape character, so a raw Windows path
        inside the quoted ``file`` directive would be mangled.
        """
        with tempfile.NamedTemporaryFile(
            suffix=".txt", mode="w", encoding="utf-8", delete=False
        ) as temp_file:
            for input_file in input_files:
                escaped = (
                    str(input_file).replace("\\", "/").replace("'", "'\\''")
                )
                temp_file.write(f"file '{escaped}'\n")
            return Path(temp_file.name)

    @staticmethod
    def _parse_timestamp(timestamp: str) -> float:
        normalized = timestamp.replace(",", ".")
        hours, minutes, seconds = normalized.split(":")
        return (
            int(hours) * 3600
            + int(minutes) * 60
            + float(seconds)
        )

    @staticmethod
    def _package_video_chain(subtitle_name: str, trim_filter: str) -> str:
        """Look, then ASS, then trim/tempo — one encode, upright text.

        ``subtitles`` stays on the source timeline. ``trim`` and
        ``setpts=PTS/tempo`` come after burn-in so lip-sync still tracks
        the sped-up output. A second encode is not needed.
        """
        return (
            f"{MediaProcessor._PACKAGE_VIDEO_FILTER},"
            f"subtitles={subtitle_name},"
            f"{trim_filter},"
            f"setpts=PTS-STARTPTS,"
            f"{MediaProcessor._PACKAGE_VIDEO_OUTPUT}"
        )

    @staticmethod
    def _white_noise_mix(duration_seconds: float) -> str:
        """Mix a -42 dB white-noise bed under the labeled ``[a0]`` program."""
        return (
            f"anoisesrc=d={duration_seconds:.3f}:s=44100:"
            f"a={PACKAGE_NOISE_AMPLITUDE}:c=white,"
            "aformat=sample_rates=44100:channel_layouts=stereo[an];"
            "[a0][an]amix=inputs=2:duration=first:"
            "dropout_transition=0:normalize=0[a]"
        )

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
    _PACKAGE_VIDEO_FILTER = (
        "scale=1960:1102:flags=bicubic,"
        f"rotate=a={PACKAGE_ROTATE_RADIANS}:"
        f"ow=rotw({PACKAGE_ROTATE_RADIANS}):"
        f"oh=roth({PACKAGE_ROTATE_RADIANS}):c=black:bilinear=0,"
        "crop=1920:1080,"
        "eq=brightness=0.02:contrast=1.03:saturation=1.05,"
        "hue=h=4,"
        "noise=alls=3:allf=t"
    )
    _PACKAGE_VIDEO_OUTPUT = (
        f"setpts=PTS/{PACKAGE_TEMPO},"
        "format=yuv420p,"
        "fps=29.94"
    )
    _PACKAGE_AUDIO_FILTER = (
        "highpass=f=50,"
        "lowpass=f=15000,"
        f"rubberband=tempo={PACKAGE_TEMPO}:pitch={PACKAGE_PITCH},"
        "aformat=sample_rates=44100:channel_layouts=stereo,"
        "volume=0.97"
    )
    _NOISE_VIDEO_FILTER = "scale=1920:1080:flags=bicubic,format=yuv420p,fps=29.94"
    _NOISE_AUDIO_FILTER = (
        "aformat=sample_rates=44100:channel_layouts=stereo"
    )
    _PACKAGE_ENCODE_ARGS = [
        "-c:v",
        "h264_nvenc",
        "-preset",
        "p5",
        "-tune",
        "hq",
        "-rc",
        "vbr",
        "-cq",
        "19",
        "-b:v",
        PACKAGE_VIDEO_BITRATE,
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
    ]
