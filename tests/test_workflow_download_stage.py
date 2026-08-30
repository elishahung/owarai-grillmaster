import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import project as project_module
import workflow.stages.media as media_stage
from project import Project
from services.package import rc as package_rc


class RecordSourceProgramTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="download-stage-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        self.projects_root = root / "projects"
        self.rc_path = root / ".packagerc"
        patcher = patch.object(
            project_module, "PROJECT_ROOT_NAME", str(self.projects_root)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        rc_patcher = patch.object(
            package_rc, "package_rc_path", return_value=self.rc_path
        )
        rc_patcher.start()
        self.addCleanup(rc_patcher.stop)

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
            json.loads(self.rc_path.read_text(encoding="utf-8")),
            {"series": {"ドキュメンタル": {}}, "channel": {"Prime Video": {}}},
        )

    def test_info_json_without_program_fields_registers_nothing(self):
        project = self._make_project({"id": "epstage1"})

        media_stage.record_source_program(project)

        self.assertIsNone(project.source_metadata.series)
        self.assertFalse(self.rc_path.exists())

    def test_missing_info_json_registers_nothing(self):
        project = self._make_project(None)

        media_stage.record_source_program(project)

        self.assertFalse(self.rc_path.exists())


if __name__ == "__main__":
    unittest.main()
