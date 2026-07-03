import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from services.media import MediaProcessor


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


if __name__ == "__main__":
    unittest.main()
