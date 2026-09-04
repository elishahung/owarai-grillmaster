import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from services.media import (
    PACKAGE_ENCODE_CONCURRENCY,
    PACKAGE_LEAD_TRIM_SECONDS,
    PACKAGE_MIN_PART_SECONDS,
    PACKAGE_OUTPUT_FPS,
    PACKAGE_TEMPO,
    MediaProcessor,
    package_output_duration,
)


class CutVideoTests(unittest.TestCase):
    def _run_cut(self, **kwargs) -> tuple[dict, dict]:
        with patch("services.media.ffmpeg") as ffmpeg_mock:
            ffmpeg_mock.input.return_value = MagicMock()
            MediaProcessor.cut_video(
                Path("in.mp4"), Path("out.mp4"), **kwargs
            )
            input_kwargs = ffmpeg_mock.input.call_args.kwargs
            output_kwargs = (
                ffmpeg_mock.input.return_value.output.call_args.kwargs
            )
        return input_kwargs, output_kwargs

    def test_both_bounds_seek_and_duration(self):
        input_kwargs, output_kwargs = self._run_cut(
            start_seconds=90.0, end_seconds=600.0
        )
        self.assertEqual(input_kwargs["ss"], 90.0)
        self.assertEqual(output_kwargs["t"], 510.0)
        self.assertEqual(output_kwargs["c"], "copy")

    def test_start_only_has_no_duration(self):
        input_kwargs, output_kwargs = self._run_cut(start_seconds=90.0)
        self.assertEqual(input_kwargs["ss"], 90.0)
        self.assertNotIn("t", output_kwargs)

    def test_end_only_has_no_seek(self):
        input_kwargs, output_kwargs = self._run_cut(end_seconds=600.0)
        self.assertNotIn("ss", input_kwargs)
        self.assertEqual(output_kwargs["t"], 600.0)

    def test_no_bounds_raises(self):
        with self.assertRaises(ValueError):
            MediaProcessor.cut_video(Path("in.mp4"), Path("out.mp4"))

    def test_empty_range_raises(self):
        with self.assertRaises(ValueError):
            MediaProcessor.cut_video(
                Path("in.mp4"),
                Path("out.mp4"),
                start_seconds=600.0,
                end_seconds=90.0,
            )


class MediaProcessorTests(unittest.TestCase):
    def test_parse_timecode_line(self):
        result = MediaProcessor.parse_timecode_line(
            "00:01:02,500 --> 00:01:05,250"
        )
        self.assertEqual(result.start_seconds, 62.5)
        self.assertEqual(result.end_seconds, 65.25)

    def test_evenly_spaced_timestamps(self):
        timestamps = MediaProcessor.evenly_spaced_timestamps(60.0, 5)
        self.assertEqual(timestamps, [10.0, 20.0, 30.0, 40.0, 50.0])

    def test_absolute_interval_timestamps_left_closed_right_open(self):
        timestamps = MediaProcessor.absolute_interval_timestamps(
            start_seconds=61.0,
            end_seconds=180.0,
            interval_seconds=60.0,
            include_start=True,
            include_end=False,
        )
        self.assertEqual(timestamps, [61.0, 120.0])

    def test_absolute_interval_timestamps_last_chunk_can_include_end(self):
        timestamps = MediaProcessor.absolute_interval_timestamps(
            start_seconds=120.0,
            end_seconds=180.0,
            interval_seconds=60.0,
            include_start=True,
            include_end=True,
        )
        self.assertEqual(timestamps, [120.0, 180.0])


class BurnInPartsTests(unittest.TestCase):
    def test_short_video_stays_one_part(self):
        parts = MediaProcessor.burn_in_parts(PACKAGE_MIN_PART_SECONDS * 2 - 1)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].start_seconds, PACKAGE_LEAD_TRIM_SECONDS)

    def test_part_count_is_capped_by_the_minimum_part_length(self):
        parts = MediaProcessor.burn_in_parts(PACKAGE_MIN_PART_SECONDS * 2)
        self.assertEqual(len(parts), 2)

    def test_long_video_uses_the_whole_encode_pool(self):
        parts = MediaProcessor.burn_in_parts(7200.0)
        self.assertEqual(len(parts), PACKAGE_ENCODE_CONCURRENCY)

    def test_parts_tile_the_trimmed_range_without_gaps(self):
        usable = 7200.0
        parts = MediaProcessor.burn_in_parts(usable)
        self.assertEqual(parts[0].start_seconds, PACKAGE_LEAD_TRIM_SECONDS)
        self.assertAlmostEqual(
            parts[-1].end_seconds, PACKAGE_LEAD_TRIM_SECONDS + usable
        )
        for earlier, later in zip(parts, parts[1:]):
            self.assertEqual(earlier.end_seconds, later.start_seconds)

    def test_boundaries_land_on_whole_output_frames(self):
        # Only the last part carries the sub-frame remainder; every boundary
        # before it must sit on a frame, or the concatenated parts would not
        # add up to the frame count a single pass produces.
        parts = MediaProcessor.burn_in_parts(7200.0)
        for part in parts[:-1]:
            frames = (
                package_output_duration(part.duration_seconds)
                * PACKAGE_OUTPUT_FPS
            )
            self.assertAlmostEqual(frames, round(frames), places=6)
        total = sum(
            package_output_duration(part.duration_seconds)
            for part in parts
        )
        self.assertAlmostEqual(total, package_output_duration(7200.0))
        self.assertAlmostEqual(
            PACKAGE_TEMPO / PACKAGE_OUTPUT_FPS,
            (parts[0].duration_seconds)
            / round(
                package_output_duration(parts[0].duration_seconds)
                * PACKAGE_OUTPUT_FPS
            ),
        )


if __name__ == "__main__":
    unittest.main()
