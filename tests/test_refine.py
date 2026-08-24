import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import project as project_module
import services.postprocess.refine as refine_module
from project import Project
from services.postprocess.refine import (
    RefinementValidationError,
    refine_subtitles,
)

_TRANSLATED_SRT = """1
00:00:01,000 --> 00:00:02,000
這是第一句

2
00:00:02,000 --> 00:00:03,000
這是第二句
"""

# What a timed-out agent leaves behind when it streams the file out block by
# block: valid SRT syntax, but the tail is missing.
_TRUNCATED_SRT = """1
00:00:01,000 --> 00:00:02,000
這是第一句潤飾後
"""


class RefineResumeTests(unittest.TestCase):
    def _make_project(self) -> Project:
        base = Path(__file__).resolve().parents[1] / "tmp_test_artifacts"
        root = base / "tmp_refine"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        patcher = patch.object(project_module, "PROJECT_ROOT_NAME", str(root))
        patcher.start()
        self.addCleanup(patcher.stop)
        project = Project(id="demo")
        project.project_path.mkdir(parents=True, exist_ok=True)
        project.translated_path.write_text(_TRANSLATED_SRT, encoding="utf-8")
        return project

    def _write_refined(self, project: Project, content: str) -> None:
        project.refined_srt_path.write_text(content, encoding="utf-8")

    def _write_report(self, project: Project) -> None:
        project.refine_report_path.parent.mkdir(parents=True, exist_ok=True)
        project.refine_report_path.write_text("| 字幕編號 |\n", encoding="utf-8")

    def _valid_agent(self, project: Project):
        def _side_effect(*args, **kwargs):
            self._write_refined(project, _TRANSLATED_SRT)
            self._write_report(project)
            return "done"

        return _side_effect

    def test_output_from_unfinished_run_is_discarded(self):
        # Reaching the stage at all means project.json never got its flag, so
        # whatever sits there is an unfinished attempt's output.
        project = self._make_project()
        self._write_refined(project, _TRUNCATED_SRT)
        self._write_report(project)

        with patch.object(
            refine_module,
            "run_inference",
            side_effect=self._valid_agent(project),
        ) as run_agent:
            refine_subtitles(project)

        run_agent.assert_called_once()
        self.assertEqual(
            project.refined_srt_path.read_text(encoding="utf-8"),
            _TRANSLATED_SRT,
        )

    def test_agent_that_writes_nothing_raises(self):
        project = self._make_project()
        self._write_refined(project, _TRANSLATED_SRT)

        with patch.object(refine_module, "run_inference") as run_agent:
            with self.assertRaises(RefinementValidationError):
                refine_subtitles(project)

        run_agent.assert_called_once()

    def test_missing_translated_source_raises(self):
        project = self._make_project()
        project.translated_path.unlink()
        self._write_refined(project, _TRANSLATED_SRT)

        with patch.object(refine_module, "run_inference") as run_agent:
            with self.assertRaises(RefinementValidationError):
                refine_subtitles(project)

        run_agent.assert_not_called()


if __name__ == "__main__":
    unittest.main()
