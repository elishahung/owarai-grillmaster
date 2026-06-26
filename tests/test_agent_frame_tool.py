"""Tests for the on-demand agent frame tool (extraction + instruction)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.media import MediaProcessor
from services.inference.tools import (
    FRAME_TOOL_SCRIPTS,
    FrameToolStage,
    build_chunk_frame_tool_instruction,
    build_frame_tool_instruction,
    build_glossary_check_frame_tool_instruction,
    build_pre_pass_frame_tool_instruction,
    build_refine_frame_tool_instruction,
)
from services.inference.tools import get_frames


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
            Path("projects/x"),
            5.0,
            65.0,
            scope_label="your assigned chunk range",
            stage=FrameToolStage.CHUNK,
        )
        self.assertIn(str(FRAME_TOOL_SCRIPTS[FrameToolStage.CHUNK]), text)
        self.assertIn("--project-dir", text)
        self.assertIn("--times", text)
        self.assertIn("extra_frames", text)
        self.assertNotIn("--out", text)
        self.assertNotIn("--stage ", text)
        self.assertNotIn("--context", text)
        self.assertNotIn("--window", text)
        self.assertIn("5.000s to 65.000s", text)
        self.assertIn("your assigned chunk range", text)
        self.assertIn("At most 20 timestamps", text)
        self.assertIn("字卡", text)  # on-screen text-card cue
        self.assertIn("ASR", text)
        self.assertNotIn("sparingly", text)
        # The agent does not control frame size: no --max-side flag exposed.
        self.assertNotIn("--max-side", text)

    def test_pre_pass_helper_encourages_proactive_visual_checks(self):
        text = build_pre_pass_frame_tool_instruction(
            Path("projects/x"),
            0.0,
            100.0,
        )
        self.assertIn("Pre-pass is the anchor", text)
        self.assertIn("proactively", text)
        self.assertIn("proper_nouns", text)
        self.assertIn("segment_summaries", text)

    def test_chunk_helper_sets_stage_local_extra_frames(self):
        text = build_chunk_frame_tool_instruction(
            Path("projects/x"),
            0.0,
            10.0,
        )
        self.assertIn(str(FRAME_TOOL_SCRIPTS[FrameToolStage.CHUNK]), text)
        self.assertIn("--project-dir", text)
        self.assertIn("extra_frames", text)

    def test_refine_helper_encourages_bounded_visual_checks(self):
        text = build_refine_frame_tool_instruction(
            Path("projects/x"),
            0.0,
            10.0,
        )
        self.assertIn(str(FRAME_TOOL_SCRIPTS[FrameToolStage.REFINE]), text)
        self.assertIn("medium polishing pass", text)
        self.assertIn("Use frames proactively", text)
        self.assertIn("routine fluency edits", text)

    def test_glossary_check_helper_sets_stage_local_extra_frames(self):
        text = build_glossary_check_frame_tool_instruction(
            Path("projects/x"),
            0.0,
            10.0,
        )
        self.assertIn(
            str(FRAME_TOOL_SCRIPTS[FrameToolStage.GLOSSARY_CHECK]), text
        )
        self.assertIn("terminology", text)
        self.assertIn("pre_pass.json", text)

    def test_window_is_clamped_non_negative_and_ordered(self):
        text = build_frame_tool_instruction(
            Path("projects/x"),
            -10.0,
            -1.0,
            scope_label="the entire video",
            stage=FrameToolStage.PRE_PASS,
        )
        # start clamped to 0; end clamped up to start.
        self.assertIn("0.000s to 0.000s", text)


class GetFramesCliTests(unittest.TestCase):
    def test_stage_wrapper_writes_to_extra_frames(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            project_dir = root / "project"
            project_dir.mkdir()
            video = project_dir / "video.mp4"
            video.write_bytes(b"not real video")
            expected_out = project_dir / ".pre_pass" / "media" / "extra_frames"
            seen: dict[str, object] = {}

            def _fake_extract_at(*, input_file, output_dir, timestamps, max_side):
                seen["input_file"] = input_file
                seen["output_dir"] = output_dir
                seen["timestamps"] = timestamps
                output_dir.mkdir(parents=True, exist_ok=True)
                return [
                    output_dir / f"frame_{timestamp:010.3f}_{max_side}.jpg"
                    for timestamp in timestamps
                ]

            with (
                patch.object(
                    MediaProcessor, "get_media_duration", return_value=100.0
                ),
                patch.object(
                    MediaProcessor,
                    "extract_frames_at",
                    side_effect=_fake_extract_at,
                ),
                patch.object(get_frames, "Settings") as mock_settings,
            ):
                mock_settings.return_value.video_frame_max_side = 768
                code = get_frames.main_for_stage(
                    FrameToolStage.PRE_PASS,
                    [
                        "--project-dir",
                        str(project_dir),
                        "--times",
                        "1,2,2,120",
                    ]
                )

            self.assertEqual(code, 0)
            self.assertEqual(seen["input_file"], project_dir / "video.mp4")
            self.assertEqual(seen["output_dir"], expected_out)
            self.assertEqual(seen["timestamps"], [1.0, 2.0, 100.0])

    def test_glossary_check_wrapper_writes_to_extra_frames(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            project_dir = root / "project"
            project_dir.mkdir()
            video = project_dir / "video.mp4"
            video.write_bytes(b"not real video")
            expected_out = project_dir / ".glossary_check" / "extra_frames"
            seen: dict[str, object] = {}

            def _fake_extract_at(*, input_file, output_dir, timestamps, max_side):
                seen["input_file"] = input_file
                seen["output_dir"] = output_dir
                seen["timestamps"] = timestamps
                output_dir.mkdir(parents=True, exist_ok=True)
                return [
                    output_dir / f"frame_{timestamp:010.3f}_{max_side}.jpg"
                    for timestamp in timestamps
                ]

            with (
                patch.object(
                    MediaProcessor, "get_media_duration", return_value=100.0
                ),
                patch.object(
                    MediaProcessor,
                    "extract_frames_at",
                    side_effect=_fake_extract_at,
                ),
                patch.object(get_frames, "Settings") as mock_settings,
            ):
                mock_settings.return_value.video_frame_max_side = 768
                code = get_frames.main_for_stage(
                    FrameToolStage.GLOSSARY_CHECK,
                    [
                        "--project-dir",
                        str(project_dir),
                        "--times",
                        "1,2,2,120",
                    ],
                )

            self.assertEqual(code, 0)
            self.assertEqual(seen["input_file"], project_dir / "video.mp4")
            self.assertEqual(seen["output_dir"], expected_out)
            self.assertEqual(seen["timestamps"], [1.0, 2.0, 100.0])

    def test_frame_tool_caps_to_twenty_timestamps(self):
        with tempfile.TemporaryDirectory() as d:
            project_dir = Path(d) / "project"
            project_dir.mkdir()
            (project_dir / "video.mp4").write_bytes(b"not real video")
            seen: dict[str, object] = {}

            def _fake_extract_at(*, input_file, output_dir, timestamps, max_side):
                seen["timestamps"] = timestamps
                output_dir.mkdir(parents=True, exist_ok=True)
                return [
                    output_dir / f"frame_{timestamp:010.3f}_{max_side}.jpg"
                    for timestamp in timestamps
                ]

            with (
                patch.object(
                    MediaProcessor, "get_media_duration", return_value=100.0
                ),
                patch.object(
                    MediaProcessor,
                    "extract_frames_at",
                    side_effect=_fake_extract_at,
                ),
                patch.object(get_frames, "Settings") as mock_settings,
            ):
                mock_settings.return_value.video_frame_max_side = 768
                get_frames.main_for_stage(
                    FrameToolStage.GLOSSARY_CHECK,
                    [
                        "--project-dir",
                        str(project_dir),
                        "--times",
                        ",".join(str(i) for i in range(25)),
                    ],
                )

            self.assertEqual(len(seen["timestamps"]), 20)
            self.assertEqual(seen["timestamps"], [float(i) for i in range(20)])


if __name__ == "__main__":
    unittest.main()
