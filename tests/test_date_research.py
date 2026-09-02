import json
import shutil
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

import project as project_module
import workflow as workflow_module
import workflow.api as workflow_api
import workflow.side_tasks as side_tasks
import workflow.stages.media as media_stage
from project import Project
from services.postprocess.date_research import (
    DateResearchResult,
    apply_date_research_result,
    load_cached_date_research,
    research_broadcast_date,
)


def _found_result_json(trust: str = "high") -> str:
    return json.dumps(
        {
            "status": "found",
            "broadcast_date": "2026-02-04",
            "trust": trust,
            "sources": [
                {
                    "url": "https://example.com/program",
                    "source_name": "Apple TV",
                    "evidence_summary": "Episode title and program match.",
                }
            ],
            "rejected_candidates": [
                {
                    "date": "2026-02-05",
                    "reason": "BiliBili upload date, not broadcast date",
                }
            ],
        }
    )


def _unknown_result_json() -> str:
    return json.dumps({"status": "unknown"})


class DateResearchTests(unittest.TestCase):
    def _make_temp_root(self) -> Path:
        base = Path(__file__).resolve().parents[1] / "tmp_test_artifacts"
        base.mkdir(parents=True, exist_ok=True)
        path = base / "tmp_date_research"
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_research_found_writes_artifact_and_returns_result(self):
        root = self._make_temp_root()
        with patch.object(project_module, "PROJECT_ROOT_NAME", str(root)):
            project = Project(id="ep123", name="demo")
            project.save()
            with patch(
                "services.postprocess.date_research.run_inference",
                return_value=SimpleNamespace(text=_found_result_json()),
            ) as mock_inference:
                result = research_broadcast_date(project)

            self.assertEqual(mock_inference.call_count, 1)
            self.assertEqual(result.status, "found")
            self.assertEqual(result.broadcast_date, date(2026, 2, 4))
            self.assertEqual(result.trust, "high")
            self.assertTrue(project.date_research_path.exists())
            persisted = DateResearchResult.model_validate_json(
                project.date_research_path.read_text(encoding="utf-8")
            )
            self.assertEqual(persisted.broadcast_date, date(2026, 2, 4))

    def test_research_unknown_still_writes_artifact(self):
        root = self._make_temp_root()
        with patch.object(project_module, "PROJECT_ROOT_NAME", str(root)):
            project = Project(id="ep123", name="demo")
            project.save()
            with patch(
                "services.postprocess.date_research.run_inference",
                return_value=SimpleNamespace(text=_unknown_result_json()),
            ):
                result = research_broadcast_date(project)

            self.assertEqual(result.status, "unknown")
            self.assertIsNone(result.broadcast_date)
            self.assertTrue(project.date_research_path.exists())

    def test_existing_artifact_skips_agent_invocation(self):
        root = self._make_temp_root()
        with patch.object(project_module, "PROJECT_ROOT_NAME", str(root)):
            project = Project(id="ep123", name="demo")
            project.artifacts_dir.mkdir(parents=True, exist_ok=True)
            project.date_research_path.write_text(
                _found_result_json(), encoding="utf-8"
            )
            with patch(
                "services.postprocess.date_research.run_inference"
            ) as mock_inference:
                result = research_broadcast_date(project)

            mock_inference.assert_not_called()
            self.assertEqual(result.broadcast_date, date(2026, 2, 4))

    def test_corrupt_artifact_is_treated_as_cache_miss(self):
        root = self._make_temp_root()
        with patch.object(project_module, "PROJECT_ROOT_NAME", str(root)):
            project = Project(id="ep123", name="demo")
            project.artifacts_dir.mkdir(parents=True, exist_ok=True)
            project.date_research_path.write_text(
                "{ truncated", encoding="utf-8"
            )
            with patch(
                "services.postprocess.date_research.run_inference",
                return_value=SimpleNamespace(text=_found_result_json()),
            ) as mock_inference:
                result = research_broadcast_date(project)

            self.assertEqual(mock_inference.call_count, 1)
            self.assertEqual(result.broadcast_date, date(2026, 2, 4))
            # The corrupt file was overwritten with the fresh valid result.
            persisted = DateResearchResult.model_validate_json(
                project.date_research_path.read_text(encoding="utf-8")
            )
            self.assertEqual(persisted.broadcast_date, date(2026, 2, 4))

    def test_load_cached_returns_none_for_missing_or_corrupt(self):
        root = self._make_temp_root()
        with patch.object(project_module, "PROJECT_ROOT_NAME", str(root)):
            project = Project(id="ep123", name="demo")
            self.assertIsNone(load_cached_date_research(project))
            project.artifacts_dir.mkdir(parents=True, exist_ok=True)
            project.date_research_path.write_text("not json", encoding="utf-8")
            self.assertIsNone(load_cached_date_research(project))

    def test_prompt_context_includes_persisted_fields(self):
        root = self._make_temp_root()
        with patch.object(project_module, "PROJECT_ROOT_NAME", str(root)):
            project = Project(
                id="ep123",
                name="260202_variety_show",
                translation_hint="番組タイトル - 企画の説明",
            )
            project.save()
            with patch(
                "services.postprocess.date_research.run_inference",
                return_value=SimpleNamespace(text=_unknown_result_json()),
            ) as mock_inference:
                research_broadcast_date(project)

            prompt = mock_inference.call_args.kwargs["prompt"]
            self.assertIn("https://tver.jp/episodes/ep123", prompt)
            self.assertIn("260202_variety_show", prompt)
            self.assertIn("番組タイトル - 企画の説明", prompt)
            self.assertNotIn("Platform-stated original broadcast year", prompt)

    def test_prompt_context_includes_archive_broadcast_year(self):
        root = self._make_temp_root()
        with patch.object(project_module, "PROJECT_ROOT_NAME", str(root)):
            project = Project(id="ep123", name="archive_rerun")
            project.update_from_source_broadcast_date_label("2018年放送")
            with patch(
                "services.postprocess.date_research.run_inference",
                return_value=SimpleNamespace(text=_unknown_result_json()),
            ) as mock_inference:
                research_broadcast_date(project)

            prompt = mock_inference.call_args.kwargs["prompt"]
            self.assertIn(
                "Platform-stated original broadcast year: 2018", prompt
            )
            self.assertIn("2018年放送", prompt)


class DateResearchResultSchemaTests(unittest.TestCase):
    def test_found_without_date_is_rejected(self):
        with self.assertRaises(ValidationError):
            DateResearchResult.model_validate(
                {"status": "found", "trust": "high"}
            )

    def test_found_without_trust_is_rejected(self):
        with self.assertRaises(ValidationError):
            DateResearchResult.model_validate(
                {"status": "found", "broadcast_date": "2026-02-04"}
            )

    def test_unknown_needs_no_date_or_trust(self):
        result = DateResearchResult.model_validate({"status": "unknown"})
        self.assertIsNone(result.broadcast_date)
        self.assertIsNone(result.trust)


class ApplyDateResearchResultTests(unittest.TestCase):
    def _make_project(self) -> Project:
        base = Path(__file__).resolve().parents[1] / "tmp_test_artifacts"
        base.mkdir(parents=True, exist_ok=True)
        root = base / "tmp_apply_date_research"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        patcher = patch.object(
            project_module, "PROJECT_ROOT_NAME", str(root)
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        project = Project(id="ep123", name="demo")
        project.save()
        return project

    def test_found_high_trust_sets_broadcast_date_and_flag(self):
        project = self._make_project()
        result = DateResearchResult.model_validate_json(_found_result_json())

        apply_date_research_result(project, result)

        self.assertEqual(project.broadcast_date, date(2026, 2, 4))
        self.assertTrue(project.is_broadcast_date_researched)
        persisted = json.loads(project.json_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["broadcast_date"], "2026-02-04")
        self.assertTrue(persisted["is_broadcast_date_researched"])

    def test_found_low_trust_still_sets_date_with_warning(self):
        project = self._make_project()
        result = DateResearchResult.model_validate_json(
            _found_result_json(trust="low")
        )

        apply_date_research_result(project, result)

        self.assertEqual(project.broadcast_date, date(2026, 2, 4))
        self.assertTrue(project.is_broadcast_date_researched)

    def test_unknown_marks_researched_without_date(self):
        project = self._make_project()
        result = DateResearchResult.model_validate_json(
            _unknown_result_json()
        )

        apply_date_research_result(project, result)

        self.assertIsNone(project.broadcast_date)
        self.assertTrue(project.is_broadcast_date_researched)


class WorkflowDateResearchGateTests(unittest.TestCase):
    """Kick-off gating and join-time apply of the async research task."""

    def _build_completed_project_mock(self) -> MagicMock:
        # Every stage complete so process_project falls through all skips and
        # only the date-research side-task logic is exercised.
        project = MagicMock()
        project.id = "demo"
        project.total_cost = 0.0
        for stage in workflow_module.ProgressStage:
            setattr(project, stage.value, True)
        project.is_cover_generated = True
        project.is_broadcast_date_researched = False
        project.broadcast_date = None
        return project

    def _run(self, project: MagicMock, *, enabled: bool, cached=None):
        found = DateResearchResult.model_validate_json(_found_result_json())
        with (
            patch.object(
                workflow_api.Project,
                "from_source_str",
                return_value=project,
            ),
            patch.object(
                side_tasks,
                "load_cached_date_research",
                return_value=cached,
            ) as load_cached,
            patch.object(
                side_tasks,
                "research_broadcast_date",
                return_value=found,
            ) as research,
            patch.object(
                side_tasks, "apply_date_research_result"
            ) as apply_result,
            patch.object(workflow_api.settings, "archived_path", None),
            patch.object(workflow_api.settings, "package_path", None),
            patch.object(
                workflow_api.settings,
                "enable_broadcast_date_agent_fallback",
                False,
            ),
            patch.object(
                workflow_api.settings, "enable_cover_generation", False
            ),
        ):
            workflow_module.process_project(
                "demo", enable_date_research=enabled
            )
        return load_cached, research, apply_result, found

    def test_enabled_and_undated_dispatches_agent_and_applies(self):
        project = self._build_completed_project_mock()
        _, research, apply_result, found = self._run(project, enabled=True)
        research.assert_called_once_with(project)
        apply_result.assert_called_once_with(project, found)

    def test_disabled_does_not_dispatch_agent(self):
        project = self._build_completed_project_mock()
        _, research, apply_result, _ = self._run(project, enabled=False)
        research.assert_not_called()
        apply_result.assert_not_called()

    def test_already_dated_skips_everything(self):
        project = self._build_completed_project_mock()
        project.broadcast_date = date(2026, 2, 4)
        load_cached, research, apply_result, _ = self._run(
            project, enabled=True
        )
        load_cached.assert_not_called()
        research.assert_not_called()
        apply_result.assert_not_called()

    def test_already_researched_skips_everything(self):
        project = self._build_completed_project_mock()
        project.is_broadcast_date_researched = True
        load_cached, research, apply_result, _ = self._run(
            project, enabled=True
        )
        load_cached.assert_not_called()
        research.assert_not_called()
        apply_result.assert_not_called()

    def test_cached_verdict_applied_even_when_disabled(self):
        project = self._build_completed_project_mock()
        cached = DateResearchResult.model_validate_json(_found_result_json())
        _, research, apply_result, _ = self._run(
            project, enabled=False, cached=cached
        )
        research.assert_not_called()
        apply_result.assert_called_once_with(project, cached)

    def test_side_tasks_join_after_later_stage_failure(self):
        project = self._build_completed_project_mock()
        project.is_video_processed = False
        project.is_cover_generated = False
        found = DateResearchResult.model_validate_json(_found_result_json())

        with (
            patch.object(
                workflow_api.Project,
                "from_source_str",
                return_value=project,
            ),
            patch.object(
                side_tasks,
                "load_cached_date_research",
                return_value=None,
            ),
            patch.object(
                side_tasks,
                "research_broadcast_date",
                return_value=found,
            ) as research,
            patch.object(side_tasks, "generate_cover") as generate_cover,
            patch.object(
                side_tasks, "apply_date_research_result"
            ) as apply_result,
            patch.object(
                media_stage,
                "process_video",
                side_effect=RuntimeError("video failed"),
            ),
            patch.object(
                workflow_api.settings,
                "enable_broadcast_date_agent_fallback",
                False,
            ),
            patch.object(
                workflow_api.settings, "enable_cover_generation", False
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "video failed"):
                workflow_module.process_project(
                    "demo",
                    enable_cover=True,
                    enable_date_research=True,
                )

        research.assert_called_once_with(project)
        generate_cover.assert_called_once_with(project)
        apply_result.assert_called_once_with(project, found)
        self.assertTrue(project.is_cover_generated)
        project.save.assert_called_once()

    def test_break_after_suppresses_forced_side_task_dispatch(self):
        project = self._build_completed_project_mock()
        project.is_broadcast_date_researched = False
        project.broadcast_date = None
        project.is_cover_generated = False

        with (
            patch.object(
                workflow_api.Project,
                "from_source_str",
                return_value=project,
            ),
            patch.object(
                side_tasks,
                "load_cached_date_research",
                return_value=None,
            ),
            patch.object(
                side_tasks,
                "research_broadcast_date",
            ) as research,
            patch.object(side_tasks, "generate_cover") as generate_cover,
        ):
            workflow_module.process_project(
                "demo",
                break_after=workflow_module.ProgressStage.DOWNLOADED,
                enable_cover=True,
                enable_date_research=True,
            )

        research.assert_not_called()
        generate_cover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
