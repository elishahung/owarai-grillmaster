import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import workflow.delivery as delivery
from services.progress import NoopProgressReporter


class WorkflowDeliveryTests(unittest.TestCase):
    def test_archived_project_is_packaged_from_archived_location(self):
        project = MagicMock()
        project.project_path = Path("projects/demo")
        project.archive.return_value = Path("archive/demo")
        project.total_cost = 1.25
        progress = NoopProgressReporter()

        with (
            patch.object(delivery.settings, "archived_path", Path("archive")),
            patch.object(delivery.settings, "package_path", Path("package")),
            patch.object(delivery, "package_project") as package_project,
        ):
            delivery.deliver_project(
                project=project,
                project_id="demo",
                progress=progress,
                remix_noise_name="sleep",
            )

        project.archive.assert_called_once_with()
        package_project.assert_called_once_with(
            project,
            Path("archive/demo"),
            Path("package"),
            progress,
            remix_noise_name="sleep",
        )


if __name__ == "__main__":
    unittest.main()
