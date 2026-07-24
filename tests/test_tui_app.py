import unittest
from pathlib import Path
from unittest.mock import MagicMock

from services.progress import PlannedStage
from services.tui.app import GrillMasterApp
from services.tui.reporter import TuiProgressReporter
from services.tui.state import PipelineState


def _stub_project(tmp: Path) -> MagicMock:
    project = MagicMock()
    project.id = "demo"
    project.name = "demo show"
    project.total_cost = 0.5
    for attr in (
        "pre_pass_path",
        "refine_report_path",
        "glossary_check_report_path",
        "date_research_path",
        "poster_cover_path",
        "translated_path",
        "ass_path",
        "finalized_srt_path",
    ):
        setattr(project, attr, tmp / f"{attr}.txt")
    return project


def _scripted_state(tmp: Path) -> tuple[PipelineState, TuiProgressReporter]:
    state = PipelineState()
    reporter = TuiProgressReporter(state)
    reporter.pipeline_started(
        _stub_project(tmp),
        [
            PlannedStage(key="metadata", label="Fetching metadata"),
            PlannedStage(key="download", label="Downloading video"),
            PlannedStage(key="chunks", label="Translating subtitles"),
            PlannedStage(key="finalize", label="Finalizing subtitles"),
            PlannedStage(
                key="cover", label="Cover generation", kind="side_task"
            ),
        ],
    )
    reporter.stage_skipped("metadata", "already-complete")
    reporter.stage_completed("download", 5.0)
    reporter.stage_started("chunks", "Translating subtitles")
    reporter.chunk_started(0, 4, 1, 40)
    reporter.chunk_finished(0, retries=0, cost=0.01)
    return state, reporter


class GrillMasterAppTests(unittest.IsolatedAsyncioTestCase):
    async def test_selection_follow_and_log_switching(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            state, reporter = _scripted_state(Path(tmp))
            app = GrillMasterApp(state)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                # Follow mode tracks the running chunks stage.
                self.assertEqual(
                    state.items[app.selection].key, "chunks"
                )

                # Arrow keys move the selection and disable follow.
                await pilot.press("down")
                self.assertFalse(app.follow)
                self.assertEqual(
                    state.items[app.selection].key, "finalize"
                )
                await pilot.press("up")
                self.assertEqual(state.items[app.selection].key, "chunks")

                # Log pane follows the selected item.
                state.append_log(None, "INFO", "chunk line")
                await pilot.press("f")
                self.assertTrue(app.follow)
                await pilot.pause()

    async def test_quit_gate_while_running_and_exit_after_finish(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            state, reporter = _scripted_state(Path(tmp))
            app = GrillMasterApp(state)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                # First q while running arms the abort gate, app stays alive.
                await pilot.press("q")
                self.assertIsNotNone(app._abort_armed_at)
                self.assertFalse(app._exit)

                # Second q aborts with 130.
                await pilot.press("q")
                await pilot.pause()
            self.assertEqual(app.return_value, 130)

    async def test_retry_key_only_fires_after_failure(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            state, reporter = _scripted_state(Path(tmp))
            retries = []
            app = GrillMasterApp(state, on_retry=lambda: retries.append(1) or True)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                # Still running: r is a no-op.
                await pilot.press("r")
                self.assertEqual(retries, [])

                reporter.pipeline_failed("abema flake")
                await pilot.pause()
                app.follow = False
                await pilot.press("r")
                self.assertEqual(retries, [1])
                self.assertTrue(app.follow)
                await pilot.press("q")
                await pilot.pause()

    async def test_quit_immediately_when_finished(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            state, reporter = _scripted_state(Path(tmp))
            reporter.chunk_finished(1, 0, 0.0)
            reporter.stage_completed("chunks", 60.0)
            reporter.stage_completed("finalize", 1.0)
            reporter.pipeline_completed()
            app = GrillMasterApp(state)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await pilot.press("q")
                await pilot.pause()
            self.assertEqual(app.return_value, 0)


if __name__ == "__main__":
    unittest.main()
