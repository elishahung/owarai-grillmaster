import unittest
from unittest.mock import MagicMock, patch

from project import ProgressStage
from workflow.runner import StageSpec, WorkflowRunner
from workflow.side_tasks import SideTaskManager
from workflow.timing import format_elapsed


def _spec(stage: ProgressStage = ProgressStage.DOWNLOADED) -> StageSpec:
    return StageSpec(
        stage=stage,
        start_message="Downloading video",
        complete_message="Video downloaded",
        skipped_message="Video already downloaded",
    )


class WorkflowRunnerTests(unittest.TestCase):
    def test_successful_stage_marks_progress_after_action(self):
        project = MagicMock()
        project.is_downloaded = False
        calls: list[str] = []

        def action() -> None:
            calls.append("action")

        project.mark_progress.side_effect = lambda stage: calls.append(
            f"mark:{stage.name}"
        )
        runner = WorkflowRunner(
            project=project,
            project_id="demo",
            break_after=None,
        )

        with (
            patch("workflow.runner.perf_counter", side_effect=[10.0, 72.345]),
            patch("workflow.runner.logger") as logger,
        ):
            should_stop = runner.run(_spec(), action)

        self.assertFalse(should_stop)
        self.assertEqual(calls, ["action", "mark:DOWNLOADED"])
        logger.success.assert_called_once_with(
            "Stage complete: Video downloaded (1m 2.3s)"
        )

    def test_failed_stage_does_not_mark_progress(self):
        project = MagicMock()
        project.is_downloaded = False
        runner = WorkflowRunner(
            project=project,
            project_id="demo",
            break_after=None,
        )

        with self.assertRaisesRegex(RuntimeError, "boom"):
            runner.run(_spec(), lambda: (_ for _ in ()).throw(RuntimeError("boom")))

        project.mark_progress.assert_not_called()

    def test_completed_break_stage_stops_without_action(self):
        project = MagicMock()
        project.is_downloaded = True
        action = MagicMock()
        runner = WorkflowRunner(
            project=project,
            project_id="demo",
            break_after=ProgressStage.DOWNLOADED,
        )

        should_stop = runner.run(_spec(), action)

        self.assertTrue(should_stop)
        action.assert_not_called()
        project.mark_progress.assert_not_called()


class SideTaskManagerTests(unittest.TestCase):
    def _project(self) -> MagicMock:
        project = MagicMock()
        project.id = "demo"
        project.is_cover_generated = False
        project.broadcast_date = None
        project.is_broadcast_date_researched = False
        return project

    def test_cover_success_sets_flag_and_saves(self):
        project = self._project()

        with (
            patch("workflow.side_tasks.generate_cover") as generate_cover,
            patch("workflow.side_tasks.perf_counter", side_effect=[1.0, 2.5]),
            patch("workflow.side_tasks.logger") as logger,
        ):
            manager = SideTaskManager(project)
            manager.start_cover_if_needed(enabled=True, allow_side_tasks=True)
            manager.join()

        generate_cover.assert_called_once_with(project)
        self.assertTrue(project.is_cover_generated)
        project.save.assert_called_once()
        logger.success.assert_called_once_with(
            "Stage complete: Cover generated (1.50s)"
        )

    def test_date_success_applies_result(self):
        project = self._project()
        result = object()

        with (
            patch(
                "workflow.side_tasks.research_broadcast_date",
                return_value=result,
            ) as research,
            patch(
                "workflow.side_tasks.apply_date_research_result"
            ) as apply_result,
            patch("workflow.side_tasks.perf_counter", side_effect=[3.0, 4.0]),
            patch("workflow.side_tasks.logger") as logger,
        ):
            manager = SideTaskManager(project)
            manager.start_date_research_if_needed(
                enabled=True,
                allow_side_tasks=True,
            )
            manager.join()

        research.assert_called_once_with(project)
        apply_result.assert_called_once_with(project, result)
        logger.success.assert_called_once_with(
            "Stage complete: Broadcast-date research (1.00s)"
        )

    def test_side_task_failure_does_not_raise(self):
        project = self._project()

        with patch(
            "workflow.side_tasks.generate_cover",
            side_effect=RuntimeError("cover failed"),
        ):
            manager = SideTaskManager(project)
            manager.start_cover_if_needed(enabled=True, allow_side_tasks=True)
            manager.join()

        self.assertFalse(project.is_cover_generated)
        project.save.assert_not_called()


class WorkflowTimingTests(unittest.TestCase):
    def test_format_elapsed_uses_compact_units(self):
        self.assertEqual(format_elapsed(1.234), "1.23s")
        self.assertEqual(format_elapsed(62.345), "1m 2.3s")
        self.assertEqual(format_elapsed(3662.345), "1h 1m 2.3s")


if __name__ == "__main__":
    unittest.main()
