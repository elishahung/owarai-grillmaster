import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import grillmaster.workflow.delivery as delivery
from grillmaster.services.live_chat import ChatLayout
from grillmaster.services.progress import NoopProgressReporter


class WorkflowDeliveryTests(unittest.TestCase):
    def test_archived_project_is_packaged_from_archived_location(self):
        project = MagicMock()
        project.project_path = Path("projects/demo")
        project.archive.return_value = Path("archive/demo")
        project.asr_cost = 1.25
        progress = NoopProgressReporter()

        with (
            patch.object(delivery.settings, "archived_path", Path("archive")),
            patch.object(delivery.settings, "package_path", Path("package")),
            patch.object(delivery, "package_project") as package_project,
        ):
            final_path = delivery.deliver_project(
                project=project,
                project_id="demo",
                progress=progress,
                remix_noise_name="sleep",
                chat_layout=ChatLayout.SIDE,
            )

        self.assertEqual(final_path, Path("archive/demo"))
        project.archive.assert_called_once_with()
        package_project.assert_called_once_with(
            project,
            Path("archive/demo"),
            Path("package"),
            progress,
            remix_noise_name="sleep",
            chat_layout=ChatLayout.SIDE,
        )

    def test_unarchived_project_final_path_is_its_working_dir(self):
        project = MagicMock()
        project.project_path = Path("projects/demo")
        project.asr_cost = 0.0

        with (
            patch.object(delivery.settings, "archived_path", None),
            patch.object(delivery.settings, "package_path", None),
        ):
            final_path = delivery.deliver_project(
                project=project,
                project_id="demo",
                progress=NoopProgressReporter(),
                remix_noise_name=None,
                chat_layout=ChatLayout.SIDE,
            )

        self.assertEqual(final_path, Path("projects/demo"))
        project.archive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
