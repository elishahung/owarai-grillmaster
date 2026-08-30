import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project import Project
from services import package as package_module
from services.package import core as package_core
from services.package import rc as package_rc
from services.package import noise as package_noise
from services.package import remix as package_remix
from services.package import titles as package_titles
from services.progress import NoopProgressReporter


class FakeProgressReporter(NoopProgressReporter):
    def __init__(self):
        self.events = []
        self._next_task = 1

    def start_stage(self, label: str, total: float | None = None):
        task_id = self._next_task
        self._next_task += 1
        self.events.append(("start_stage", task_id, label, total))
        return task_id

    def advance(
        self, task_id, amount: float = 1.0, description: str | None = None
    ):
        self.events.append(("advance", task_id, amount, description))

    def finish(self, task_id, status: str = "done"):
        self.events.append(("finish", task_id, status))


class PackageTests(unittest.TestCase):
    def setUp(self) -> None:
        # Title suggestion is an agent call; keep it off unless a test opts in
        # (the maintainer's .env may well have it enabled).
        patcher = patch.object(
            package_titles.settings, "enable_package_title_suggestion", False
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _make_temp_dir(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="package-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    def _write_srt(self, path: Path, ranges: list[tuple[str, str]]) -> None:
        blocks = []
        for index, (start, end) in enumerate(ranges, start=1):
            blocks.append(f"{index}\n{start} --> {end}\nLine {index}\n")
        path.write_text("\n".join(blocks), encoding="utf-8")

    def test_select_remix_segments_keeps_short_video_as_one_piece(self):
        root = self._make_temp_dir()
        srt = root / "video.cht.finalized.srt"
        self._write_srt(
            srt,
            [
                ("00:00:00,000", "00:00:10,000"),
                ("00:01:10,000", "00:01:20,000"),
            ],
        )

        segments = package_module.select_remix_segments(
            srt, duration_seconds=100.0
        )

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].start_seconds, 0.0)
        self.assertEqual(segments[0].end_seconds, 100.0)

    def test_select_remix_segments_snaps_eight_minute_cut_to_gap(self):
        root = self._make_temp_dir()
        srt = root / "video.cht.finalized.srt"
        self._write_srt(
            srt,
            [
                ("00:00:00,000", "00:07:50,000"),
                ("00:08:10,000", "00:16:40,000"),
            ],
        )

        segments = package_module.select_remix_segments(
            srt, duration_seconds=1000.0
        )

        self.assertEqual(
            [(item.start_seconds, item.end_seconds) for item in segments],
            [(0.0, 480.0), (480.0, 1000.0)],
        )

    def test_select_remix_segments_falls_back_to_nearest_boundary(self):
        root = self._make_temp_dir()
        srt = root / "video.cht.finalized.srt"
        self._write_srt(
            srt,
            [
                ("00:00:00,000", "00:06:40,000"),
                ("00:06:40,000", "00:16:40,000"),
            ],
        )

        segments = package_module.select_remix_segments(
            srt, duration_seconds=1000.0
        )

        self.assertEqual(
            [(item.start_seconds, item.end_seconds) for item in segments],
            [(0.0, 400.0), (400.0, 1000.0)],
        )

    def test_select_remix_segments_cuts_each_eight_minutes(self):
        root = self._make_temp_dir()
        srt = root / "video.cht.finalized.srt"
        self._write_srt(
            srt,
            [
                ("00:00:00,000", "00:07:50,000"),
                ("00:08:10,000", "00:15:50,000"),
                ("00:16:10,000", "00:25:00,000"),
            ],
        )

        segments = package_module.select_remix_segments(
            srt, duration_seconds=1500.0
        )

        self.assertEqual(
            [(item.start_seconds, item.end_seconds) for item in segments],
            [(0.0, 480.0), (480.0, 960.0), (960.0, 1500.0)],
        )

    def test_select_remix_segments_absorbs_tiny_remainder(self):
        root = self._make_temp_dir()
        srt = root / "video.cht.finalized.srt"
        self._write_srt(
            srt,
            [
                ("00:00:00,000", "00:07:50,000"),
                ("00:08:10,000", "00:08:20,000"),
            ],
        )

        segments = package_module.select_remix_segments(
            srt, duration_seconds=500.0
        )

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].end_seconds, 500.0)

    def test_select_remix_segments_rejects_empty_srt(self):
        root = self._make_temp_dir()
        srt = root / "video.cht.finalized.srt"
        srt.write_text("no timecodes", encoding="utf-8")

        with self.assertRaises(package_module.RemixPackageError):
            package_module.select_remix_segments(srt, duration_seconds=100.0)

    def _make_noise_dir(self, root: Path, names: list[str]) -> Path:
        noise_dir = root / "noise" / "sleep"
        noise_dir.mkdir(parents=True)
        for name in names:
            (noise_dir / name).write_text("source", encoding="utf-8")
        return noise_dir

    def _patch_noise_durations(self, durations: dict[str, float]):
        return patch.object(
            package_noise.MediaProcessor,
            "get_media_duration",
            side_effect=lambda path: durations[path.name],
        )

    def test_reserve_noise_cuts_advances_within_one_source(self):
        root = self._make_temp_dir()
        noise_dir = self._make_noise_dir(root, ["000.mp4", "001.mp4"])
        (noise_dir / "state.json").write_text(
            json.dumps({"next_index": 0, "next_seconds": 120}),
            encoding="utf-8",
        )

        with self._patch_noise_durations({"000.mp4": 600.0, "001.mp4": 600.0}):
            selection = package_module.reserve_noise_cuts(noise_dir)

        self.assertEqual(
            [
                (cut.source.name, cut.start_seconds, cut.duration_seconds)
                for cut in selection.cuts
            ],
            [("000.mp4", 120.0, 60.0), ("000.mp4", 180.0, 60.0)],
        )
        self.assertEqual(selection.next_index, 0)
        self.assertEqual(selection.next_seconds, 240)

    def test_reserve_noise_cuts_keeps_an_exactly_full_remainder(self):
        root = self._make_temp_dir()
        noise_dir = self._make_noise_dir(root, ["000.mp4"])

        with self._patch_noise_durations({"000.mp4": 240.0}):
            selection = package_module.reserve_noise_cuts(
                noise_dir, cut_count=3
            )

        self.assertEqual(
            [cut.duration_seconds for cut in selection.cuts],
            [60.0, 60.0, 60.0],
        )
        self.assertEqual(selection.next_index, 0)
        self.assertEqual(selection.next_seconds, 180)

    def test_reserve_noise_cuts_swallows_a_short_remainder(self):
        root = self._make_temp_dir()
        noise_dir = self._make_noise_dir(root, ["000.webm", "001.mp4"])
        (noise_dir / "state.json").write_text(
            json.dumps({"next_index": 0, "next_seconds": 120}),
            encoding="utf-8",
        )

        with self._patch_noise_durations(
            {"000.webm": 220.0, "001.mp4": 600.0}
        ):
            selection = package_module.reserve_noise_cuts(noise_dir)

        self.assertEqual(
            [
                (cut.source.name, cut.start_seconds, cut.duration_seconds)
                for cut in selection.cuts
            ],
            [("000.webm", 120.0, 100.0), ("001.mp4", 0.0, 60.0)],
        )
        self.assertEqual(selection.next_index, 1)
        self.assertEqual(selection.next_seconds, 60)

    def test_reserve_noise_cuts_wraps_back_to_the_first_source(self):
        root = self._make_temp_dir()
        noise_dir = self._make_noise_dir(root, ["000.mp4", "001.webm"])

        with self._patch_noise_durations(
            {"000.mp4": 100.0, "001.webm": 100.0}
        ):
            selection = package_module.reserve_noise_cuts(noise_dir)

        self.assertEqual(
            [
                (cut.source.name, cut.start_seconds, cut.duration_seconds)
                for cut in selection.cuts
            ],
            [("000.mp4", 0.0, 100.0), ("001.webm", 0.0, 100.0)],
        )
        self.assertEqual(selection.next_index, 0)
        self.assertEqual(selection.next_seconds, 0)

    def test_reserve_noise_cuts_orders_mixed_extensions_by_index(self):
        root = self._make_temp_dir()
        noise_dir = self._make_noise_dir(
            root, ["001.mp4", "002.mkv", "000.webm"]
        )

        with self._patch_noise_durations(
            {"000.webm": 60.0, "001.mp4": 60.0, "002.mkv": 60.0}
        ):
            selection = package_module.reserve_noise_cuts(
                noise_dir, cut_count=3
            )

        self.assertEqual(
            [cut.source.name for cut in selection.cuts],
            ["000.webm", "001.mp4", "002.mkv"],
        )
        self.assertEqual(selection.next_index, 0)
        self.assertEqual(selection.next_seconds, 0)

    def test_reserve_noise_cuts_rejects_an_empty_noise_folder(self):
        root = self._make_temp_dir()
        noise_dir = self._make_noise_dir(root, [])

        with self.assertRaises(package_module.RemixPackageError):
            package_module.reserve_noise_cuts(noise_dir)

    def test_reserve_noise_cuts_rejects_non_contiguous_sources(self):
        root = self._make_temp_dir()
        noise_dir = self._make_noise_dir(
            root, ["000.mp4", "001.mp4", "003.mp4"]
        )

        with self.assertRaises(package_module.RemixPackageError):
            package_module.reserve_noise_cuts(noise_dir)

    def test_noise_state_is_reserved_before_the_first_render(self):
        root = self._make_temp_dir()
        source = root / "source"
        package_root = root / "package"
        noise_dir = package_root / "noise" / "sleep"
        source.mkdir()
        noise_dir.mkdir(parents=True)
        for index in range(4):
            (noise_dir / f"{index:03d}.mp4").write_text(
                "source", encoding="utf-8"
            )
        (source / "video.mp4").write_text("video", encoding="utf-8")
        (source / "video.cht.ass").write_text("ass", encoding="utf-8")
        self._write_srt(
            source / "video.cht.finalized.srt",
            [
                ("00:00:00,000", "00:07:50,000"),
                ("00:08:10,000", "00:16:40,000"),
            ],
        )
        project = Project(id="demo", name="show")

        def fail_on_second_output(**kwargs):
            if kwargs["output_file"].name == "video_2.mp4":
                raise subprocess.CalledProcessError(1, ["ffmpeg"])
            kwargs["output_file"].write_text("ok", encoding="utf-8")

        with (
            patch.object(
                package_remix.MediaProcessor,
                "get_media_duration",
                return_value=1000.0,
            ),
            patch.object(
                package_remix.MediaProcessor,
                "build_remix_output",
                side_effect=fail_on_second_output,
            ),
        ):
            package_module.package_project(
                project,
                source,
                package_root,
                remix_noise_name="sleep",
            )

        # The cursor is a reservation, so a failed render still consumes it:
        # a concurrent package run must never draw the same noise.
        state = json.loads((noise_dir / "state.json").read_text("utf-8"))
        self.assertEqual(state, {"next_index": 0, "next_seconds": 240})
        self.assertFalse((package_root / "demo_show").exists())

    def test_normal_package_writes_video_and_cover(self):
        root = self._make_temp_dir()
        source = root / "source"
        package_root = root / "package"
        source.mkdir()
        (source / "video.mp4").write_text("video", encoding="utf-8")
        (source / "video.cht.ass").write_text("ass", encoding="utf-8")
        (source / "poster.cover.png").write_text("cover", encoding="utf-8")
        (source / ".pre_pass").mkdir()
        (source / ".pre_pass" / "pre_pass.json").write_text(
            '{"summary":"demo"}', encoding="utf-8"
        )
        (source / ".refine").mkdir()
        (source / ".refine" / "report.md").write_text(
            "refine report", encoding="utf-8"
        )
        (source / ".glossary_check").mkdir()
        (source / ".glossary_check" / "report.md").write_text(
            "glossary report", encoding="utf-8"
        )
        project = Project(id="demo", name="show")

        def create_video(**kwargs):
            kwargs["output_file"].write_text("burned", encoding="utf-8")

        with patch.object(
            package_core.MediaProcessor,
            "burn_in_subtitles",
            side_effect=create_video,
        ):
            package_module.package_project(project, source, package_root)

        target = package_root / "demo_show"
        self.assertEqual(
            (target / "video.mp4").read_text(encoding="utf-8"), "burned"
        )
        self.assertTrue((target / "cover.png").exists())
        self.assertEqual(
            (target / "pre_pass.json").read_text(encoding="utf-8"),
            '{"summary":"demo"}',
        )
        self.assertEqual(
            (target / "refine.md").read_text(encoding="utf-8"),
            "refine report",
        )
        self.assertEqual(
            (target / "glossary_check.md").read_text(encoding="utf-8"),
            "glossary report",
        )

    def test_package_keeps_output_when_auxiliary_artifacts_are_missing(self):
        root = self._make_temp_dir()
        source = root / "source"
        package_root = root / "package"
        source.mkdir()
        (source / "video.mp4").write_text("video", encoding="utf-8")
        (source / "video.cht.ass").write_text("ass", encoding="utf-8")
        project = Project(id="demo", name="show")

        def create_video(**kwargs):
            kwargs["output_file"].write_text("burned", encoding="utf-8")

        with patch.object(
            package_core.MediaProcessor,
            "burn_in_subtitles",
            side_effect=create_video,
        ):
            package_module.package_project(project, source, package_root)

        target = package_root / "demo_show"
        self.assertEqual(
            (target / "video.mp4").read_text(encoding="utf-8"), "burned"
        )
        self.assertFalse((target / "pre_pass.json").exists())
        self.assertFalse((target / "refine.md").exists())
        self.assertFalse((target / "glossary_check.md").exists())

    def test_remix_package_writes_two_videos_cover_and_state(self):
        root = self._make_temp_dir()
        source = root / "source"
        package_root = root / "package"
        noise_dir = package_root / "noise" / "sleep"
        source.mkdir()
        noise_dir.mkdir(parents=True)
        for index in range(4):
            (noise_dir / f"{index:03d}.mp4").write_text(
                f"source {index}", encoding="utf-8"
            )
        (source / "video.mp4").write_text("video", encoding="utf-8")
        (source / "video.cht.ass").write_text("ass", encoding="utf-8")
        (source / "poster.jpg").write_text("cover", encoding="utf-8")
        (source / ".pre_pass").mkdir()
        (source / ".pre_pass" / "pre_pass.json").write_text(
            '{"summary":"remix"}', encoding="utf-8"
        )
        self._write_srt(
            source / "video.cht.finalized.srt",
            [
                ("00:00:00,000", "00:07:50,000"),
                ("00:08:10,000", "00:16:40,000"),
            ],
        )
        project = Project(id="demo", name="show")
        calls = []

        def create_remix_output(**kwargs):
            calls.append(kwargs)
            kwargs["output_file"].write_text("remix", encoding="utf-8")

        def media_duration(path):
            return 1000.0 if path.name == "video.mp4" else 150.0

        with (
            patch.object(
                package_remix.MediaProcessor,
                "get_media_duration",
                side_effect=media_duration,
            ),
            patch.object(
                package_remix.MediaProcessor,
                "build_remix_output",
                side_effect=create_remix_output,
            ),
        ):
            package_module.package_project(
                project,
                source,
                package_root,
                remix_noise_name="sleep",
            )

        self.assertEqual(
            [
                (
                    call["output_file"].name,
                    (
                        call["head_noise"].source.name,
                        call["head_noise"].start_seconds,
                        call["head_noise"].duration_seconds,
                    ),
                    (
                        call["tail_noise"].source.name,
                        call["tail_noise"].start_seconds,
                        call["tail_noise"].duration_seconds,
                    ),
                    call["start_seconds"],
                    call["end_seconds"],
                )
                for call in calls
            ],
            [
                (
                    "video_1.mp4",
                    ("000.mp4", 0.0, 60.0),
                    ("000.mp4", 60.0, 90.0),
                    3.0,
                    480.0,
                ),
                (
                    "video_2.mp4",
                    ("001.mp4", 0.0, 60.0),
                    ("001.mp4", 60.0, 90.0),
                    480.0,
                    1000.0,
                ),
            ],
        )
        target = package_root / "demo_show"
        self.assertTrue((target / "video_1.mp4").exists())
        self.assertTrue((target / "video_2.mp4").exists())
        self.assertFalse((target / "video_3.mp4").exists())
        self.assertTrue((target / "cover.jpg").exists())
        self.assertEqual(
            (target / "video_1.mp4").read_text(encoding="utf-8"),
            "remix",
        )
        self.assertEqual(
            (target / "pre_pass.json").read_text(encoding="utf-8"),
            '{"summary":"remix"}',
        )
        state = json.loads((noise_dir / "state.json").read_text("utf-8"))
        self.assertEqual(state, {"next_index": 2, "next_seconds": 0})

    def test_remix_package_uses_one_progress_task_for_two_target_renders(self):
        root = self._make_temp_dir()
        source = root / "source"
        package_root = root / "package"
        target = package_root / "demo_show"
        noise_dir = package_root / "noise" / "sleep"
        source.mkdir()
        target.mkdir(parents=True)
        noise_dir.mkdir(parents=True)
        for index in range(4):
            (noise_dir / f"{index:03d}.mp4").write_text(
                "source", encoding="utf-8"
            )
        (source / "video.mp4").write_text("video", encoding="utf-8")
        (source / "video.cht.ass").write_text("ass", encoding="utf-8")
        self._write_srt(
            source / "video.cht.finalized.srt",
            [
                ("00:00:00,000", "00:07:50,000"),
                ("00:08:10,000", "00:16:40,000"),
            ],
        )
        progress = FakeProgressReporter()

        def advance_render(**kwargs):
            kwargs["progress"].advance(
                kwargs["progress_task"],
                kwargs["end_seconds"] - kwargs["start_seconds"],
                description=kwargs["output_file"].name,
            )
            kwargs["output_file"].write_text("remix", encoding="utf-8")

        with (
            patch.object(
                package_remix.MediaProcessor,
                "get_media_duration",
                return_value=1000.0,
            ),
            patch.object(
                package_remix.MediaProcessor,
                "build_remix_output",
                side_effect=advance_render,
            ),
        ):
            package_remix.package_remix(
                source_root=source,
                package_root=package_root,
                target_dir=target,
                video_file=source / "video.mp4",
                subtitle_file=source / "video.cht.ass",
                noise_name="sleep",
                progress=progress,
            )

        self.assertEqual(
            progress.events[0],
            ("start_stage", 1, "Remixing subtitles", 1240.0),
        )
        self.assertIn(("advance", 1, 477.0, "video_1.mp4"), progress.events)
        self.assertIn(("advance", 1, 520.0, "video_2.mp4"), progress.events)
        self.assertEqual(progress.events[-1], ("finish", 1, "done"))

    def test_packagerc_series_rule_forces_a_remix_package(self):
        root = self._make_temp_dir()
        source = root / "source"
        package_root = root / "package"
        noise_dir = package_root / "noise" / "default"
        source.mkdir()
        noise_dir.mkdir(parents=True)
        for index in range(2):
            (noise_dir / f"{index:03d}.mp4").write_text(
                f"source {index}", encoding="utf-8"
            )
        (source / "video.mp4").write_text("video", encoding="utf-8")
        (source / "video.cht.ass").write_text("ass", encoding="utf-8")
        self._write_srt(
            source / "video.cht.finalized.srt",
            [("00:00:00,000", "00:01:40,000")],
        )
        rc_path = root / ".packagerc"
        rc_path.write_text(
            json.dumps({"series": {"ドキュメンタル": {"remix": True}}}),
            encoding="utf-8",
        )
        project = Project(id="demo", name="show")
        project.source_metadata.series = "ドキュメンタル"

        def create_remix_output(**kwargs):
            kwargs["output_file"].write_text("remix", encoding="utf-8")

        with (
            patch.object(
                package_rc, "package_rc_path", return_value=rc_path
            ),
            patch.object(
                package_remix.MediaProcessor,
                "get_media_duration",
                return_value=100.0,
            ),
            patch.object(
                package_remix.MediaProcessor,
                "build_remix_output",
                side_effect=create_remix_output,
            ),
            patch.object(
                package_core.MediaProcessor, "burn_in_subtitles"
            ) as burn_in_subtitles,
        ):
            package_module.package_project(project, source, package_root)

        burn_in_subtitles.assert_not_called()
        self.assertEqual(
            (package_root / "demo_show" / "video_1.mp4").read_text(
                encoding="utf-8"
            ),
            "remix",
        )

    def test_packagerc_remix_without_default_noise_fails_the_package(self):
        root = self._make_temp_dir()
        source = root / "source"
        package_root = root / "package"
        source.mkdir()
        (source / "video.mp4").write_text("video", encoding="utf-8")
        (source / "video.cht.ass").write_text("ass", encoding="utf-8")
        self._write_srt(
            source / "video.cht.finalized.srt",
            [("00:00:00,000", "00:01:40,000")],
        )
        rc_path = root / ".packagerc"
        rc_path.write_text(
            json.dumps({"channel": {"テレビ東京": {"remix": True}}}),
            encoding="utf-8",
        )
        project = Project(id="demo", name="show")
        project.source_metadata.channel = "テレビ東京"

        with (
            patch.object(
                package_rc, "package_rc_path", return_value=rc_path
            ),
            patch.object(
                package_remix.MediaProcessor,
                "get_media_duration",
                return_value=100.0,
            ),
            patch.object(
                package_core.MediaProcessor, "burn_in_subtitles"
            ) as burn_in_subtitles,
        ):
            package_module.package_project(project, source, package_root)

        # A forced remix never degrades into a plain burn-in.
        burn_in_subtitles.assert_not_called()
        self.assertFalse((package_root / "demo_show").exists())

    def test_package_project_directory_uses_project_json(self):
        root = self._make_temp_dir()
        project_dir = root / "project"
        package_root = root / "package"
        project_dir.mkdir()
        (project_dir / "project.json").write_text(
            Project(id="demo", name="show").model_dump_json(),
            encoding="utf-8",
        )

        with patch.object(package_core, "package_project") as package_project:
            package_core.package_project_directory(project_dir, package_root)

        self.assertEqual(package_project.call_args.kwargs["source_root"], project_dir)
        self.assertEqual(
            package_project.call_args.kwargs["package_root"], package_root
        )


if __name__ == "__main__":
    unittest.main()
