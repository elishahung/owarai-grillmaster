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
from services.package import remix as package_remix


class FakeProgressReporter:
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
    def _make_temp_dir(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="package-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    def _write_srt(self, path: Path, ranges: list[tuple[str, str]]) -> None:
        blocks = []
        for index, (start, end) in enumerate(ranges, start=1):
            blocks.append(f"{index}\n{start} --> {end}\nLine {index}\n")
        path.write_text("\n".join(blocks), encoding="utf-8")

    def test_select_remix_split_prefers_nearest_middle_gap(self):
        root = self._make_temp_dir()
        srt = root / "video.cht.finalized.srt"
        self._write_srt(
            srt,
            [
                ("00:00:00,000", "00:00:10,000"),
                ("00:00:30,000", "00:00:35,000"),
                ("00:01:10,000", "00:01:20,000"),
            ],
        )

        split = package_module.select_remix_split(srt, duration_seconds=100.0)

        self.assertEqual(split, 52.5)

    def test_select_remix_split_falls_back_to_nearest_boundary(self):
        root = self._make_temp_dir()
        srt = root / "video.cht.finalized.srt"
        self._write_srt(
            srt,
            [
                ("00:00:00,000", "00:00:40,000"),
                ("00:00:40,000", "00:01:40,000"),
            ],
        )

        split = package_module.select_remix_split(srt, duration_seconds=100.0)

        self.assertEqual(split, 40.0)

    def test_select_remix_split_rejects_empty_srt(self):
        root = self._make_temp_dir()
        srt = root / "video.cht.finalized.srt"
        srt.write_text("no timecodes", encoding="utf-8")

        with self.assertRaises(package_module.RemixPackageError):
            package_module.select_remix_split(srt, duration_seconds=100.0)

    def test_select_noise_chunks_wraps_and_advances(self):
        root = self._make_temp_dir()
        noise_dir = root / "noise" / "sleep"
        noise_dir.mkdir(parents=True)
        for index in range(5):
            (noise_dir / f"{index:03d}.mp4").write_text(
                "chunk", encoding="utf-8"
            )
        (noise_dir / "state.json").write_text(
            json.dumps({"next_index": 3}), encoding="utf-8"
        )

        selection = package_module.select_noise_chunks(noise_dir)

        self.assertEqual(
            [path.name for path in selection.chunk_paths],
            ["003.mp4", "004.mp4", "000.mp4"],
        )
        self.assertEqual(selection.next_index, 1)

    def test_select_noise_chunks_rejects_less_than_three_chunks(self):
        root = self._make_temp_dir()
        noise_dir = root / "noise" / "sleep"
        noise_dir.mkdir(parents=True)
        for index in range(2):
            (noise_dir / f"{index:03d}.mp4").write_text(
                "chunk", encoding="utf-8"
            )

        with self.assertRaises(package_module.RemixPackageError):
            package_module.select_noise_chunks(noise_dir)

    def test_select_noise_chunks_rejects_non_contiguous_chunks(self):
        root = self._make_temp_dir()
        noise_dir = root / "noise" / "sleep"
        noise_dir.mkdir(parents=True)
        for name in ["000.mp4", "001.mp4", "003.mp4", "004.mp4"]:
            (noise_dir / name).write_text("chunk", encoding="utf-8")

        with self.assertRaises(package_module.RemixPackageError):
            package_module.select_noise_chunks(noise_dir)

    def test_write_noise_state_happens_only_after_successful_remix(self):
        root = self._make_temp_dir()
        source = root / "source"
        package_root = root / "package"
        noise_dir = package_root / "noise" / "sleep"
        source.mkdir()
        noise_dir.mkdir(parents=True)
        for index in range(3):
            (noise_dir / f"{index:03d}.mp4").write_text(
                "chunk", encoding="utf-8"
            )
        (source / "video.mp4").write_text("video", encoding="utf-8")
        (source / "video.cht.ass").write_text("ass", encoding="utf-8")
        self._write_srt(
            source / "video.cht.finalized.srt",
            [("00:00:00,000", "00:00:01,000")],
        )
        project = Project(id="demo", name="show")

        def fail_on_second_output(**kwargs):
            if kwargs["output_file"].name == "video_3.mp4":
                raise subprocess.CalledProcessError(1, ["ffmpeg"])
            kwargs["output_file"].write_text("ok", encoding="utf-8")

        with (
            patch.object(
                package_remix.MediaProcessor,
                "get_media_duration",
                return_value=10.0,
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

        self.assertFalse((noise_dir / "state.json").exists())
        self.assertFalse((package_root / "demo_show").exists())

    def test_normal_package_writes_video_and_cover(self):
        root = self._make_temp_dir()
        source = root / "source"
        package_root = root / "package"
        source.mkdir()
        (source / "video.mp4").write_text("video", encoding="utf-8")
        (source / "video.cht.ass").write_text("ass", encoding="utf-8")
        (source / "poster.cover.png").write_text("cover", encoding="utf-8")
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

    def test_remix_package_writes_three_videos_cover_and_state(self):
        root = self._make_temp_dir()
        source = root / "source"
        package_root = root / "package"
        noise_dir = package_root / "noise" / "sleep"
        source.mkdir()
        noise_dir.mkdir(parents=True)
        for index in range(4):
            (noise_dir / f"{index:03d}.mp4").write_text(
                f"chunk {index}", encoding="utf-8"
            )
        (source / "video.mp4").write_text("video", encoding="utf-8")
        (source / "video.cht.ass").write_text("ass", encoding="utf-8")
        (source / "poster.jpg").write_text("cover", encoding="utf-8")
        self._write_srt(
            source / "video.cht.finalized.srt",
            [
                ("00:00:00,000", "00:00:01,000"),
                ("00:00:04,000", "00:00:05,000"),
            ],
        )
        project = Project(id="demo", name="show")

        def create_remix_output(**kwargs):
            kwargs["output_file"].write_text("remix", encoding="utf-8")

        with (
            patch.object(
                package_remix.MediaProcessor,
                "get_media_duration",
                return_value=6.0,
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

        target = package_root / "demo_show"
        self.assertTrue((target / "video_1.mp4").exists())
        self.assertTrue((target / "video_2.mp4").exists())
        self.assertTrue((target / "video_3.mp4").exists())
        self.assertTrue((target / "cover.jpg").exists())
        self.assertEqual(
            (target / "video_1.mp4").read_text(encoding="utf-8"),
            "chunk 0",
        )
        state = json.loads((noise_dir / "state.json").read_text("utf-8"))
        self.assertEqual(state["next_index"], 3)

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
                "chunk", encoding="utf-8"
            )
        (source / "video.mp4").write_text("video", encoding="utf-8")
        (source / "video.cht.ass").write_text("ass", encoding="utf-8")
        self._write_srt(
            source / "video.cht.finalized.srt",
            [
                ("00:00:00,000", "00:00:01,000"),
                ("00:00:04,000", "00:00:05,000"),
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
                return_value=6.0,
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
            ("start_stage", 1, "Remixing subtitles", 6.0),
        )
        self.assertIn(("advance", 1, 2.5, "video_2.mp4"), progress.events)
        self.assertIn(("advance", 1, 3.5, "video_3.mp4"), progress.events)
        self.assertEqual(progress.events[-1], ("finish", 1, "done"))

    def test_remix_package_copies_noise_then_uses_one_noise_chunk_per_render(self):
        root = self._make_temp_dir()
        source = root / "source"
        package_root = root / "package"
        target = package_root / "demo_show"
        noise_dir = package_root / "noise" / "sleep"
        source.mkdir()
        target.mkdir(parents=True)
        noise_dir.mkdir(parents=True)
        for index in range(3):
            (noise_dir / f"{index:03d}.mp4").write_text(
                f"chunk {index}", encoding="utf-8"
            )
        (source / "video.mp4").write_text("video", encoding="utf-8")
        (source / "video.cht.ass").write_text("ass", encoding="utf-8")
        self._write_srt(
            source / "video.cht.finalized.srt",
            [
                ("00:00:00,000", "00:00:01,000"),
                ("00:00:04,000", "00:00:05,000"),
            ],
        )
        calls = []

        def record_render(**kwargs):
            calls.append(kwargs)
            kwargs["output_file"].write_text("remix", encoding="utf-8")

        with (
            patch.object(
                package_remix.MediaProcessor,
                "get_media_duration",
                return_value=6.0,
            ),
            patch.object(
                package_remix.MediaProcessor,
                "build_remix_output",
                side_effect=record_render,
            ),
        ):
            package_remix.package_remix(
                source_root=source,
                package_root=package_root,
                target_dir=target,
                video_file=source / "video.mp4",
                subtitle_file=source / "video.cht.ass",
                noise_name="sleep",
            )

        self.assertEqual(
            [call["noise_file"].name for call in calls],
            ["001.mp4", "002.mp4"],
        )
        self.assertEqual(
            (target / "video_1.mp4").read_text(encoding="utf-8"),
            "chunk 0",
        )

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
