import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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

    async def test_copy_log_puts_the_whole_buffer_on_the_clipboard(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            state, reporter = _scripted_state(Path(tmp))
            # More lines than the log pane can show, so a copy that only
            # took the visible tail would come up short.
            for i in range(30):
                state.append_log(None, "INFO", f"chunk line {i}")
            app = GrillMasterApp(state)
            with patch("services.tui.app.put_on_clipboard") as copy:
                async with app.run_test(size=(120, 40)) as pilot:
                    await pilot.pause()
                    await pilot.press("c")
            copied = copy.call_args.args[0].splitlines()
            self.assertEqual(copied[0], "# Translating subtitles")
            self.assertEqual(
                copied[1:], [f"chunk line {i}" for i in range(30)]
            )

    async def test_copy_log_reports_a_clipboard_failure(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            state, reporter = _scripted_state(Path(tmp))
            state.append_log(None, "INFO", "chunk line")
            app = GrillMasterApp(state)
            with (
                patch(
                    "services.tui.app.put_on_clipboard",
                    side_effect=FileNotFoundError("no clip.exe"),
                ),
                patch.object(GrillMasterApp, "notify") as notify,
            ):
                async with app.run_test(size=(120, 40)) as pilot:
                    await pilot.pause()
                    await pilot.press("c")
            self.assertEqual(notify.call_args.kwargs["severity"], "error")

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

    async def test_cover_widget_survives_archived_image_path(self):
        # A 1x1 transparent PNG; enough for textual-image/PIL to open.
        import base64
        import tempfile

        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
            "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        with tempfile.TemporaryDirectory() as tmp:
            state, reporter = _scripted_state(Path(tmp))
            cover_path = state.project.poster_cover_path
            cover_path.write_bytes(png)
            reporter.side_task_started("cover", "Cover generation")
            reporter.side_task_completed("cover", 2.0, "cover.png")
            app = GrillMasterApp(state)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                # Select the cover side task (chunks → finalize → cover).
                await pilot.press("down", "down")
                app.refresh_all()
                await pilot.pause()
                if app._image_cls is not None:
                    self.assertTrue(app.query("#cover-image"))

                # Archive moves the project folder away; the cached preview
                # still points at the old path. The refresh must not crash.
                cover_path.unlink()
                app.refresh_all()
                await pilot.pause()
                self.assertFalse(app.query("#cover-image"))

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
