import threading
import unittest
from unittest.mock import MagicMock

from loguru import logger

from services.progress import PlannedStage
from services.tui.reporter import TuiProgressReporter
from services.tui.state import ChunkState, ItemState, PipelineState


def _plan() -> list[PlannedStage]:
    return [
        PlannedStage(key="metadata", label="Fetching metadata"),
        PlannedStage(key="download", label="Downloading video"),
        PlannedStage(key="chunks", label="Translating subtitles"),
        PlannedStage(
            key="refine", label="Refining subtitles", enabled=False
        ),
        PlannedStage(key="finalize", label="Finalizing subtitles"),
        PlannedStage(key="date", label="Date research", kind="side_task"),
        PlannedStage(key="cover", label="Cover generation", kind="side_task"),
    ]


class PipelineStateTests(unittest.TestCase):
    def setUp(self):
        self.state = PipelineState()
        self.reporter = TuiProgressReporter(self.state)
        self.reporter.pipeline_started(MagicMock(total_cost=1.5), _plan())

    def test_plan_creates_items_with_disabled_states(self):
        keys = [item.key for item in self.state.items]
        self.assertEqual(
            keys,
            [
                "metadata",
                "download",
                "chunks",
                "refine",
                "finalize",
                "date",
                "cover",
            ],
        )
        self.assertIs(
            self.state.get("refine").state, ItemState.DISABLED
        )
        self.assertIs(
            self.state.get("metadata").state, ItemState.PENDING
        )

    def test_stage_lifecycle(self):
        self.reporter.stage_skipped("metadata", "already-complete")
        self.assertIs(self.state.get("metadata").state, ItemState.CACHED)

        self.reporter.stage_started("download", "Downloading video")
        self.assertIs(self.state.get("download").state, ItemState.RUNNING)
        self.assertEqual(self.state.current_stage_key, "download")

        self.reporter.stage_completed("download", 12.5)
        item = self.state.get("download")
        self.assertIs(item.state, ItemState.DONE)
        self.assertEqual(item.elapsed, 12.5)
        self.assertIsNone(self.state.current_stage_key)

    def test_pipeline_failure_marks_running_stage(self):
        self.reporter.stage_started("download", "Downloading video")
        self.reporter.pipeline_failed("boom")
        self.assertTrue(self.state.finished)
        self.assertTrue(self.state.failed)
        item = self.state.get("download")
        self.assertIs(item.state, ItemState.FAILED)
        self.assertEqual(item.error, "boom")

    def test_side_task_lifecycle_and_failure(self):
        self.reporter.side_task_started("date", "Date research")
        self.assertIs(self.state.get("date").state, ItemState.RUNNING)
        self.reporter.side_task_completed("date", 3.0, "2025-07-19")
        self.assertIs(self.state.get("date").state, ItemState.DONE)
        self.assertEqual(self.state.get("date").result, "2025-07-19")

        self.reporter.side_task_started("cover", "Cover generation")
        self.reporter.side_task_failed("cover", "codex died")
        self.assertIs(self.state.get("cover").state, ItemState.FAILED)
        self.assertEqual(self.state.get("cover").error, "codex died")

    def test_chunk_board_tracks_cells_and_totals(self):
        self.reporter.stage_started("chunks", "Translating subtitles")
        self.reporter.chunk_started(0, 3, 1, 40)
        self.reporter.chunk_started(1, 3, 41, 80)
        self.assertEqual(self.state.chunks.total, 3)
        self.assertEqual(len(self.state.chunks.active), 2)

        self.reporter.chunk_finished(0, retries=1, cost=0.02)
        self.reporter.chunk_failed(2, "bad", retries=2, cost=0.01)
        board = self.state.chunks
        self.assertEqual(board.done, 1)
        self.assertEqual(board.failed, 1)
        self.assertEqual(board.retries, 3)
        self.assertAlmostEqual(board.cost, 0.03)
        self.assertIs(board.cells[0].state, ChunkState.DONE)
        self.assertIs(board.cells[2].state, ChunkState.FAILED)

    def test_total_progress_weights_cached_and_disabled(self):
        for key in ("metadata", "download", "chunks", "finalize"):
            self.reporter.stage_skipped(key, "already-complete")
        # refine is disabled and must not count against the total.
        self.assertAlmostEqual(self.state.total_progress(), 1.0)

    def test_bar_attribution_by_thread_name(self):
        self.reporter.stage_started("download", "Downloading video")

        task_holder = {}

        def side_task_bar():
            task_holder["cover"] = self.reporter.start_stage(
                "Burning subtitles", total=10.0
            )

        thread = threading.Thread(target=side_task_bar, name="cover_0")
        thread.start()
        thread.join()
        main_task = self.reporter.start_stage("Downloading 0.mp4", total=1.0)

        self.assertIn(task_holder["cover"], self.state.get("cover").bars)
        self.assertIn(main_task, self.state.get("download").bars)

        self.reporter.advance(main_task, 0.5, description="half")
        bar = self.state.get("download").bars[main_task]
        self.assertAlmostEqual(bar.fraction, 0.5)
        self.assertEqual(bar.description, "half")
        self.reporter.finish(main_task, "done")
        self.assertTrue(bar.done)

    def test_reset_for_new_attempt_clears_failure_and_chunks(self):
        self.reporter.stage_started("chunks", "Translating subtitles")
        self.reporter.chunk_started(0, 3, 1, 40)
        self.reporter.pipeline_failed("boom")

        self.state.reset_for_new_attempt()

        self.assertFalse(self.state.finished)
        self.assertFalse(self.state.failed)
        self.assertIsNone(self.state.error)
        self.assertEqual(self.state.chunks.total, 0)
        self.assertIsNone(self.state.current_stage_key)
        # A rerun rebuilds the items via pipeline_started.
        self.reporter.pipeline_started(MagicMock(total_cost=0.0), _plan())
        self.assertIs(
            self.state.get("metadata").state, ItemState.PENDING
        )

    def test_log_sink_routes_by_thread_name(self):
        self.reporter.stage_started("download", "Downloading video")
        self.reporter.install_logging()
        try:
            logger.info("main pipeline line")

            def side_log():
                logger.warning("cover line")

            thread = threading.Thread(
                target=side_log, name="date-research_0"
            )
            thread.start()
            thread.join()
        finally:
            self.reporter.restore_logging()

        download_log = list(self.state.get("download").log)
        date_log = list(self.state.get("date").log)
        self.assertEqual(len(download_log), 1)
        self.assertIn("main pipeline line", download_log[0][1])
        self.assertEqual(download_log[0][0], "INFO")
        self.assertEqual(len(date_log), 1)
        self.assertIn("cover line", date_log[0][1])
        self.assertEqual(date_log[0][0], "WARNING")


if __name__ == "__main__":
    unittest.main()
