import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import workflow.serial as serial
from services.progress import NoopProgressReporter
from workflow import SerialRun


class _Reporter(NoopProgressReporter):
    def __init__(self) -> None:
        self.batch_items: list[tuple[int, int, str]] = []

    def batch_item_started(self, index: int, total: int, source: str) -> None:
        self.batch_items.append((index, total, source))


class SerialRunTests(unittest.TestCase):
    def test_each_project_is_seeded_by_the_previous_final_dir(self):
        progress = _Reporter()
        run = SerialRun(
            sources=["BV1", "BV2", "BV3"],
            parent_project_path=Path("archive/ep0"),
            submit_kwargs={"enable_cover": True},
        )
        finals = [Path("archive/ep1"), Path("archive/ep2"), Path("p/ep3")]

        with patch.object(
            serial, "submit_project", side_effect=finals
        ) as submit_project:
            last = run.run(progress)

        self.assertEqual(last, Path("p/ep3"))
        self.assertEqual(run.position, 3)
        self.assertEqual(
            submit_project.call_args_list,
            [
                call(
                    source_str=source,
                    parent_project_path=parent,
                    progress=progress,
                    enable_cover=True,
                )
                for source, parent in zip(
                    ["BV1", "BV2", "BV3"], [Path("archive/ep0"), *finals]
                )
            ],
        )
        self.assertEqual(
            progress.batch_items,
            [(1, 3, "BV1"), (2, 3, "BV2"), (3, 3, "BV3")],
        )

    def test_failure_stops_the_chain_and_a_rerun_resumes_there(self):
        run = SerialRun(sources=["BV1", "BV2", "BV3"])
        submit_project = MagicMock(side_effect=[Path("a/ep1"), RuntimeError("boom")])

        with patch.object(serial, "submit_project", submit_project):
            with self.assertRaises(RuntimeError):
                run.run()

        self.assertEqual(run.position, 1)
        self.assertEqual(run.parent_project_path, Path("a/ep1"))
        self.assertEqual(
            run.resume_command(),
            f"grill serial BV2 BV3 --parent-project {Path('a/ep1')}",
        )

        submit_project.side_effect = [Path("a/ep2"), Path("a/ep3")]
        with patch.object(serial, "submit_project", submit_project):
            run.run()

        # BV1 is not re-submitted; BV2 picks up BV1's final dir.
        self.assertEqual(submit_project.call_count, 4)
        resumed = submit_project.call_args_list[2].kwargs
        self.assertEqual(resumed["source_str"], "BV2")
        self.assertEqual(resumed["parent_project_path"], Path("a/ep1"))
        self.assertEqual(run.position, 3)

    def test_sources_are_validated_before_anything_runs(self):
        with self.assertRaises(ValueError):
            SerialRun(sources=[])
        with self.assertRaises(ValueError):
            SerialRun(sources=["https://example.com/watch/1", "BV1"])
        with self.assertRaises(ValueError):
            SerialRun(sources=["BV1", "https://www.bilibili.com/video/BV1"])


if __name__ == "__main__":
    unittest.main()
