"""Media processing utilities for audio extraction, chunk slicing, and frames.

This module provides the MediaProcessor class for handling common media operations
such as extracting audio from video files, slicing chunk audio, sampling frames,
and combining multiple video files.
"""

from collections import deque
from pathlib import Path
import ffmpeg
import subprocess
import tempfile
import os
import threading
from loguru import logger
from pydantic import BaseModel
from tqdm import tqdm


class TimeRange(BaseModel):
    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_seconds - self.start_seconds)


class MediaProcessor:
    """A utility class for processing media files using ffmpeg.

    This class provides static methods for common media processing tasks including
    audio extraction and video concatenation.
    """

    @staticmethod
    def extract_audio(input_file: Path, output_file: Path) -> Path:
        """Extract audio from a video file and convert it to Opus format.

        The audio is extracted with the following settings:
        - Mono channel (ac=1)
        - 16kHz sample rate (ar=16000)
        - 24k bitrate

        Args:
            input_file: Path to the input video file.

        Returns:
            Path to the output audio file with .opus extension.

        Raises:
            ffmpeg.Error: If the extraction process fails.
        """
        logger.info(f"Extracting audio from video: {input_file}")
        try:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            ffmpeg.input(str(input_file)).output(
                str(output_file),
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
            file_list_content = "\n".join(
                [f"file '{input_file}'" for input_file in sorted(input_files)]
            )

            with tempfile.NamedTemporaryFile(
                suffix=".txt", delete=False
            ) as temp_file:
                temp_file.write(file_list_content.encode())
                temp_file_path = temp_file.name

            logger.debug(f"Concatenating videos using ffmpeg")
            ffmpeg.input(
                f"concat:{temp_file_path}", format="concat", safe=0
            ).output(
                str(output_file),
                c="copy",
                map=0,
                movflags="faststart",
            ).run(
                overwrite_output=True
            )

            logger.debug("Cleaning up temporary and input files")
            os.remove(temp_file_path)
            for input_file in input_files:
                input_file.unlink()

            logger.success(f"Successfully combined videos into: {output_file}")
        except Exception as e:
            logger.error(f"Failed to combine videos: {e}")
            raise

    @staticmethod
    def burn_in_subtitles(
        video_file: Path,
        subtitle_file: Path,
        output_file: Path,
    ) -> None:
        """Burn ASS/SRT subtitles into the video.

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
        duration_seconds = MediaProcessor.get_media_duration(video_file)
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-progress",
            "pipe:1",
            "-i",
            video_file.name,
            "-vf",
            f"subtitles={subtitle_file.name}",
            "-c:a",
            "copy",
            str(output_file),
            "-y",
        ]
        stderr_lines: deque[str] = deque(maxlen=20)

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
                stderr_lines.append(line.rstrip())

        stderr_thread = threading.Thread(target=collect_stderr, daemon=True)
        stderr_thread.start()

        with tqdm(
            total=duration_seconds,
            desc="Burning subtitles",
            unit="s",
            dynamic_ncols=True,
        ) as progress:
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
                progress.update(max(0.0, current_seconds - progress.n))

            return_code = process.wait()
            stderr_thread.join()
            if return_code == 0:
                progress.update(max(0.0, duration_seconds - progress.n))

        if return_code != 0:
            stderr_tail = "\n".join(stderr_lines)
            logger.error(
                f"ffmpeg burn-in failed (exit {return_code}): {stderr_tail}"
            )
            raise subprocess.CalledProcessError(
                return_code,
                cmd,
                stderr=stderr_tail,
            )

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
    def _parse_timestamp(timestamp: str) -> float:
        normalized = timestamp.replace(",", ".")
        hours, minutes, seconds = normalized.split(":")
        return (
            int(hours) * 3600
            + int(minutes) * 60
            + float(seconds)
        )
