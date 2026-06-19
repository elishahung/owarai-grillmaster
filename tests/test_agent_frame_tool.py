"""Tests for the on-demand agent frame tool (extraction + instruction)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.media import MediaProcessor
from services.inference.tools import (
    FRAME_TOOL_SCRIPT,
    build_frame_tool_instruction,
)


class ExtractFramesTests(unittest.TestCase):
    def test_at_extracts_each_timestamp(self):
        seen: list[float] = []

        def _fake(*, input_file, output_file, timestamp_seconds, max_side):
            seen.append(timestamp_seconds)
            return output_file

        with (
            tempfile.TemporaryDirectory() as d,
            patch.object(
                MediaProcessor, "extract_video_frame", side_effect=_fake
            ),
        ):
            paths = MediaProcessor.extract_frames_at(
                input_file=Path("v.mp4"),
                output_dir=Path(d),
                timestamps=[12.5, 15.0, 17.5],
                max_side=512,
            )

        self.assertEqual(seen, [12.5, 15.0, 17.5])
        self.assertEqual(len(paths), 3)
        self.assertTrue(all(p.name.endswith("_512.jpg") for p in paths))

    def test_at_skips_failures(self):
        def _fake(*, input_file, output_file, timestamp_seconds, max_side):
            if timestamp_seconds == 2.0:
                raise RuntimeError("boom")
            return output_file

        with (
            tempfile.TemporaryDirectory() as d,
            patch.object(
                MediaProcessor, "extract_video_frame", side_effect=_fake
            ),
        ):
            paths = MediaProcessor.extract_frames_at(
                input_file=Path("v.mp4"),
                output_dir=Path(d),
                timestamps=[1.0, 2.0, 3.0],
                max_side=768,
            )

        self.assertEqual(len(paths), 2)  # the failing timestamp is dropped


class FrameToolInstructionTests(unittest.TestCase):
    def test_contains_command_window_and_cues(self):
        text = build_frame_tool_instruction(
            Path("projects/x/video.mp4"),
            5.0,
            65.0,
            scope_label="your assigned chunk range",
        )
        self.assertIn(str(FRAME_TOOL_SCRIPT), text)
        self.assertIn("video.mp4", text)
        self.assertIn("--times", text)
        self.assertIn("5.000s to 65.000s", text)
        self.assertIn("your assigned chunk range", text)
        self.assertIn("字卡", text)  # on-screen text-card cue
        self.assertIn("ASR", text)
        # The agent does not control frame size: no --max-side flag exposed.
        self.assertNotIn("--max-side", text)

    def test_out_dir_renders_relative_out_flag(self):
        # pre-pass / chunk write into the agent's cwd so sandboxed backends
        # (gemini-cli) can read the frames back without copying them.
        with_out = build_frame_tool_instruction(
            Path("v.mp4"), 0.0, 10.0,
            scope_label="the entire video", out_dir="agent_frames",
        )
        self.assertIn("--out agent_frames", with_out)

        # refine omits --out (cwd is the project dir; default is a temp dir).
        without_out = build_frame_tool_instruction(
            Path("v.mp4"), 0.0, 10.0, scope_label="the entire video",
        )
        self.assertNotIn("--out", without_out)

    def test_window_is_clamped_non_negative_and_ordered(self):
        text = build_frame_tool_instruction(
            Path("v.mp4"),
            -10.0,
            -1.0,
            scope_label="the entire video",
        )
        # start clamped to 0; end clamped up to start.
        self.assertIn("0.000s to 0.000s", text)


if __name__ == "__main__":
    unittest.main()
