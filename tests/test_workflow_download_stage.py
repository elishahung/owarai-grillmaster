import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import grillmaster.project as project_module
import grillmaster.workflow.stages.media as media_stage
from grillmaster.project import Project
from grillmaster.services.media import TimeRange
from grillmaster.services.program_config import config as program_config


class RecordSourceProgramTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="download-stage-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        self.projects_root = root / "projects"
        self.config_file = root / "config.json"
        patcher = patch.object(
            project_module, "PROJECT_ROOT_NAME", str(self.projects_root)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        config_patcher = patch.object(
            program_config, "config_path", return_value=self.config_file
        )
        config_patcher.start()
        self.addCleanup(config_patcher.stop)

    def _make_project(self, info_json: dict | None) -> Project:
        project = Project(id="epstage1", name="demo")
        project.save()
        if info_json is not None:
            project.metadata_info_path.write_text(
                json.dumps(info_json), encoding="utf-8"
            )
        return project

    def test_download_records_program_and_registers_rules(self):
        project = self._make_project(
            {"series": "ドキュメンタル", "channel": "Prime Video"}
        )

        media_stage.record_source_program(project)

        self.assertEqual(project.source_metadata.series, "ドキュメンタル")
        self.assertEqual(project.source_metadata.channel, "Prime Video")
        self.assertEqual(
            json.loads(self.config_file.read_text(encoding="utf-8")),
            {
                "$schema": "./config.schema.json",
                "series": {"ドキュメンタル": {}},
                "channel": {"Prime Video": {}},
            },
        )

    def test_info_json_without_program_fields_registers_nothing(self):
        project = self._make_project({"id": "epstage1"})

        media_stage.record_source_program(project)

        self.assertIsNone(project.source_metadata.series)
        self.assertFalse(self.config_file.exists())

    def test_missing_info_json_registers_nothing(self):
        project = self._make_project(None)

        media_stage.record_source_program(project)

        self.assertFalse(self.config_file.exists())


class VerifyDownloadedAudioTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="download-verify-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        patcher = patch.object(
            project_module, "PROJECT_ROOT_NAME", str(root / "projects")
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.project = Project(id="epverify1", name="demo")
        self.project.save()
        (self.project.project_path / "0.mp4").write_bytes(b"")

    def test_gapped_download_fails_and_names_the_file(self):
        gap = TimeRange(start_seconds=141.781, end_seconds=150.14)
        with patch.object(
            media_stage.MediaProcessor, "find_audio_gaps", return_value=[gap]
        ):
            with self.assertRaisesRegex(
                ValueError, r"0\.mp4 is missing audio at 141\.78s→150\.14s"
            ):
                media_stage.verify_downloaded_audio(self.project)

    def test_contiguous_download_passes(self):
        with patch.object(
            media_stage.MediaProcessor, "find_audio_gaps", return_value=[]
        ) as find:
            media_stage.verify_downloaded_audio(self.project)

        find.assert_called_once_with(self.project.project_path / "0.mp4")


if __name__ == "__main__":
    unittest.main()
