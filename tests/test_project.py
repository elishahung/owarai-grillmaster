import json
import os
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import project as project_module
import services.paths as paths_module
from project import Project, VideoSource
from services.ytdlp.info import SourceProgramInfo, SourceTalentInfo


class ProjectTests(unittest.TestCase):
    def _make_temp_dir(self) -> Path:
        base = Path(__file__).resolve().parents[1] / "tmp_test_artifacts"
        base.mkdir(parents=True, exist_ok=True)
        path = base / "tmp_project"
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_legacy_project_loads_with_default_cost_fields(self):
        root = self._make_temp_dir()
        project_id = "legacy-project"
        project_dir = root / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "project.json").write_text(
            json.dumps({"id": project_id, "name": "legacy"}),
            encoding="utf-8",
        )

        with patch.object(project_module, "PROJECT_ROOT_NAME", str(root)):
            loaded = Project.from_source_str(project_id)

        self.assertEqual(loaded.total_cost, 0.0)
        self.assertEqual(loaded.service_costs, {})

    def test_add_cost_updates_project_json_totals(self):
        root = self._make_temp_dir()
        with patch.object(project_module, "PROJECT_ROOT_NAME", str(root)):
            project = Project(id="cost-project", name="demo")
            project.save()

            project.add_cost("gemini", 1.25)
            project.add_cost("gemini", 0.75)
            project.add_cost("elevenlabs", 2.0)

            persisted = json.loads(
                project.json_path.read_text(encoding="utf-8")
            )

        self.assertEqual(project.total_cost, 4.0)
        self.assertEqual(project.service_costs["gemini"], 2.0)
        self.assertEqual(project.service_costs["elevenlabs"], 2.0)
        self.assertEqual(persisted["total_cost"], 4.0)
        self.assertEqual(persisted["service_costs"]["gemini"], 2.0)
        self.assertEqual(persisted["service_costs"]["elevenlabs"], 2.0)

    def test_intermediate_paths_use_hidden_cache_dirs(self):
        root = self._make_temp_dir()
        with patch.object(project_module, "PROJECT_ROOT_NAME", str(root)):
            project = Project(id="layout-project", name="demo")

            self.assertEqual(
                project.audio_path,
                root / "layout-project" / ".asr" / "audio.ogg",
            )
            self.assertEqual(
                project.asr_path,
                root / "layout-project" / ".asr" / "asr.json",
            )
            self.assertEqual(
                project.pre_pass_path,
                root / "layout-project" / ".pre_pass" / "pre_pass.json",
            )

    def test_tver_talents_persist_in_project_metadata_context(self):
        root = self._make_temp_dir()
        with patch.object(project_module, "PROJECT_ROOT_NAME", str(root)):
            project = Project(id="epmetadata1", name="demo")
            project.update_from_source_talents(
                [
                    SourceTalentInfo(
                        id="t001",
                        name="濱家　隆一",
                        name_kana="ハマイエ　リュウイチ",
                        roles=["お笑い芸人"],
                    )
                ]
            )

            persisted = json.loads(
                project.json_path.read_text(encoding="utf-8")
            )
            context = project.source_metadata_context()

        self.assertEqual(
            persisted["source_metadata"]["talents"][0]["name"],
            "濱家　隆一",
        )
        self.assertIn("濱家　隆一 / ハマイエ　リュウイチ", context)
        self.assertIn("お笑い芸人", context)


class SourceProgramTests(unittest.TestCase):
    def _make_temp_dir(self) -> Path:
        base = Path(__file__).resolve().parents[1] / "tmp_test_artifacts"
        base.mkdir(parents=True, exist_ok=True)
        path = base / "tmp_program_project"
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_series_and_channel_persist_in_project_json(self):
        root = self._make_temp_dir()
        with patch.object(project_module, "PROJECT_ROOT_NAME", str(root)):
            project = Project(id="epprogram1", name="demo")
            project.update_from_source_program(
                SourceProgramInfo(
                    series="ドキュメンタル", channel="Prime Video"
                )
            )
            persisted = json.loads(
                project.json_path.read_text(encoding="utf-8")
            )

        self.assertEqual(
            persisted["source_metadata"]["series"], "ドキュメンタル"
        )
        self.assertEqual(
            persisted["source_metadata"]["channel"], "Prime Video"
        )

    def test_absent_fields_keep_previously_captured_names(self):
        root = self._make_temp_dir()
        with patch.object(project_module, "PROJECT_ROOT_NAME", str(root)):
            project = Project(id="epprogram2", name="demo")
            project.update_from_source_program(
                SourceProgramInfo(series="ドキュメンタル", channel="Prime Video")
            )
            project.update_from_source_program(
                SourceProgramInfo(channel="テレビ東京")
            )

        self.assertEqual(project.source_metadata.series, "ドキュメンタル")
        self.assertEqual(project.source_metadata.channel, "テレビ東京")


class ArchiveLayoutTests(unittest.TestCase):
    def _make_temp_dir(self, name: str) -> Path:
        base = Path(__file__).resolve().parents[1] / "tmp_test_artifacts"
        base.mkdir(parents=True, exist_ok=True)
        path = base / name
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_archive_dir_dated_uses_yy_mm_subdirs(self):
        project = Project(
            id="ep123", name="demo", broadcast_date=date(2026, 5, 3)
        )
        archived_root = self._make_temp_dir("tmp_archive_dest")
        self.assertEqual(
            project.archive_dir(archived_root),
            archived_root / "26" / "05" / "260503_ep123_demo",
        )

    def test_archive_dir_undated_falls_back_to_etc(self):
        project = Project(id="ep123", name="demo")
        archived_root = self._make_temp_dir("tmp_archive_dest")
        self.assertEqual(
            project.archive_dir(archived_root),
            archived_root / "etc" / "ep123_demo",
        )

    def test_archive_dir_trims_long_name_to_fit_path_limit(self):
        project = Project(
            id="ep123", name="a" * 200, broadcast_date=date(2026, 5, 3)
        )
        archived_root = self._make_temp_dir("tmp_archive_dest")
        parent = archived_root / "26" / "05"
        # Room for the identity prefix plus 30 characters of the title.
        limit = (
            len(str(parent))
            + 1
            + len(project.deliverable_stem)
            + 30
            + project_module.PROJECT_INNER_PATH_RESERVE
        )

        with patch.object(paths_module, "MAX_PATH_UNITS", limit):
            archive_dir = project.archive_dir(archived_root)

        # Identity prefix survives, the title is trimmed, and the deepest
        # nested artifact still fits within the (patched) limit.
        self.assertEqual(archive_dir.name, f"260503_ep123_{'a' * 29}")
        self.assertLess(len(archive_dir.name), len(project.deliverable_name))
        self.assertLessEqual(
            len(str(parent)) + 1 + len(archive_dir.name),
            limit - project_module.PROJECT_INNER_PATH_RESERVE,
        )

    def test_package_dir_drops_name_when_root_leaves_no_room(self):
        project = Project(
            id="ep123", name="a" * 200, broadcast_date=date(2026, 5, 3)
        )
        package_root = self._make_temp_dir("tmp_package_dest")
        limit = (
            len(str(package_root.resolve()))
            + 1
            + len(project.deliverable_stem)
            + project_module.PACKAGE_INNER_PATH_RESERVE
        )

        with patch.object(paths_module, "MAX_PATH_UNITS", limit):
            package_dir = project.package_dir(package_root)

        self.assertEqual(package_dir.name, "260503_ep123")

    def _archive(self, project: Project) -> Path:
        root = self._make_temp_dir("tmp_archive_projects")
        archived_root = self._make_temp_dir("tmp_archive_dest")
        project_dir = root / project.id
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "project.json").write_text("{}", encoding="utf-8")

        with (
            patch.object(project_module, "PROJECT_ROOT_NAME", str(root)),
            patch.object(
                project_module.settings, "archived_path", archived_root
            ),
        ):
            result = project.archive()

        self.assertEqual(result, project.archive_dir(archived_root))
        self.assertTrue(result.is_dir())
        self.assertFalse(project_dir.exists())
        return archived_root

    def test_archive_moves_dated_project_into_yy_mm(self):
        project = Project(
            id="ep123", name="demo", broadcast_date=date(2026, 5, 3)
        )
        archived_root = self._archive(project)
        self.assertTrue(
            (archived_root / "26" / "05" / "260503_ep123_demo").is_dir()
        )

    def test_archive_moves_undated_project_into_etc(self):
        project = Project(id="ep123", name="demo")
        archived_root = self._archive(project)
        self.assertTrue((archived_root / "etc" / "ep123_demo").is_dir())

    def test_archive_replaces_only_the_leaf_dir(self):
        project = Project(
            id="ep123", name="demo", broadcast_date=date(2026, 5, 3)
        )
        root = self._make_temp_dir("tmp_archive_projects")
        archived_root = self._make_temp_dir("tmp_archive_dest")
        project_dir = root / project.id
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "project.json").write_text("{}", encoding="utf-8")

        # Pre-existing leaf with stale content, plus a sibling project in
        # the same YY/MM dir that must survive the rmtree.
        leaf = project.archive_dir(archived_root)
        leaf.mkdir(parents=True, exist_ok=True)
        (leaf / "stale.txt").write_text("old", encoding="utf-8")
        sibling = leaf.parent / "260510_other_show"
        sibling.mkdir(parents=True, exist_ok=True)

        with (
            patch.object(project_module, "PROJECT_ROOT_NAME", str(root)),
            patch.object(
                project_module.settings, "archived_path", archived_root
            ),
        ):
            result = project.archive()

        self.assertEqual(result, leaf)
        self.assertFalse((leaf / "stale.txt").exists())
        self.assertTrue((leaf / "project.json").exists())
        self.assertTrue(sibling.is_dir())


class SourceParsingTests(unittest.TestCase):
    def test_parse_youtube_watch_url(self):
        self.assertEqual(
            Project.parse_source_str(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            ),
            "v=dQw4w9WgXcQ",
        )

    def test_parse_youtube_watch_url_with_extra_params(self):
        self.assertEqual(
            Project.parse_source_str(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s&list=ABC"
            ),
            "v=dQw4w9WgXcQ",
        )

    def test_parse_youtube_short_url(self):
        self.assertEqual(
            Project.parse_source_str("https://youtu.be/dQw4w9WgXcQ"),
            "v=dQw4w9WgXcQ",
        )

    def test_parse_youtube_shorts_url(self):
        self.assertEqual(
            Project.parse_source_str(
                "https://www.youtube.com/shorts/abc123XYZ_-"
            ),
            "v=abc123XYZ_-",
        )

    def test_parse_youtube_live_url(self):
        self.assertEqual(
            Project.parse_source_str(
                "https://www.youtube.com/live/abc123XYZ_-"
            ),
            "v=abc123XYZ_-",
        )

    def test_parse_youtube_mobile_url(self):
        self.assertEqual(
            Project.parse_source_str(
                "https://m.youtube.com/watch?v=dQw4w9WgXcQ"
            ),
            "v=dQw4w9WgXcQ",
        )

    def test_parse_youtube_v_prefix_passthrough(self):
        # An already-stored ID must round-trip unchanged.
        self.assertEqual(
            Project.parse_source_str("v=dQw4w9WgXcQ"),
            "v=dQw4w9WgXcQ",
        )

    def test_youtube_source_detection(self):
        self.assertEqual(
            Project(id="v=dQw4w9WgXcQ").source, VideoSource.YOUTUBE
        )

    def test_youtube_source_url(self):
        self.assertEqual(
            Project(id="v=dQw4w9WgXcQ").source_url,
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )

    def test_parse_abema_slot_url(self):
        self.assertEqual(
            Project.parse_source_str(
                "https://abema.tv/channels/special-plus/slots/DGzv6KEKhRHpe3"
            ),
            "DGzv6KEKhRHpe3",
        )

    def test_abema_slot_source_url(self):
        # Slot IDs are pure alphanumeric; they must rebuild as a slots URL
        # (the channel segment is a placeholder yt-dlp never reads).
        self.assertEqual(
            Project(id="DGzv6KEKhRHpe3").source_url,
            "https://abema.tv/channels/_/slots/DGzv6KEKhRHpe3",
        )

    def test_abema_episode_source_url(self):
        self.assertEqual(
            Project(id="90-979_s1_p360").source_url,
            "https://abema.tv/video/episode/90-979_s1_p360",
        )

    def test_existing_sources_not_regressed(self):
        self.assertEqual(
            Project(id="BV1ZArvBaEqL").source, VideoSource.BILIBILI
        )
        self.assertEqual(
            Project(id="epknhe0jz5").source, VideoSource.TVER
        )
        self.assertEqual(
            Project(id="90-979_s1_p360").source, VideoSource.ABEMA
        )

    def test_existing_local_directory_is_rejected(self):
        root = Path(tempfile.mkdtemp(prefix="source-dir-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        archived = root / "26" / "08" / "260827_ep3dxmhg0g_demo"
        archived.mkdir(parents=True)
        (archived / "project.json").write_text(
            json.dumps({"id": "ep3dxmhg0g", "name": "demo"}),
            encoding="utf-8",
        )

        with self.assertRaises(ValueError) as ctx:
            Project.parse_source_str(str(archived))
        message = str(ctx.exception)
        self.assertIn("Local directory is not a video source", message)
        self.assertIn("grill package", message)

        with self.assertRaises(ValueError):
            Project.from_source_str(str(archived))

    def test_bare_id_is_accepted_even_if_cwd_has_matching_dir(self):
        root = Path(tempfile.mkdtemp(prefix="source-cwd-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        (root / "epknhe0jz5").mkdir()
        previous = Path.cwd()
        try:
            os.chdir(root)
            self.assertEqual(
                Project.parse_source_str("epknhe0jz5"), "epknhe0jz5"
            )
        finally:
            os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
