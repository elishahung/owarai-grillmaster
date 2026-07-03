import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main as main_module


class MainCliTests(unittest.TestCase):
    def _make_temp_dir(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="main-cli-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    def test_package_command_uses_configured_package_path(self):
        root = self._make_temp_dir()
        project_dir = root / "project"
        package_root = root / "package"
        project_dir.mkdir()
        progress = object()

        with (
            patch.object(main_module.settings, "package_path", package_root),
            patch.object(
                main_module, "create_progress_reporter"
            ) as create_progress_reporter,
            patch.object(
                main_module, "package_project_directory"
            ) as package_project_directory,
        ):
            create_progress_reporter.return_value.__enter__.return_value = (
                progress
            )
            main_module.main(
                ["package", str(project_dir), "--remix", "sleep"]
            )

        package_project_directory.assert_called_once_with(
            project_dir=project_dir,
            package_root=package_root,
            remix_noise_name="sleep",
            remix_prefix=False,
            progress=progress,
        )

    def test_package_command_accepts_remix_prefix(self):
        root = self._make_temp_dir()
        project_dir = root / "project"
        package_root = root / "package"
        project_dir.mkdir()
        progress = object()

        with (
            patch.object(main_module.settings, "package_path", package_root),
            patch.object(
                main_module, "create_progress_reporter"
            ) as create_progress_reporter,
            patch.object(
                main_module, "package_project_directory"
            ) as package_project_directory,
        ):
            create_progress_reporter.return_value.__enter__.return_value = (
                progress
            )
            main_module.main(
                [
                    "package",
                    str(project_dir),
                    "--remix",
                    "sleep",
                    "--prefix",
                ]
            )

        package_project_directory.assert_called_once_with(
            project_dir=project_dir,
            package_root=package_root,
            remix_noise_name="sleep",
            remix_prefix=True,
            progress=progress,
        )

    def test_noise_command_uses_configured_package_path_with_progress(self):
        root = self._make_temp_dir()
        package_root = root / "package"
        progress = object()

        with (
            patch.object(main_module.settings, "package_path", package_root),
            patch.object(
                main_module, "create_progress_reporter"
            ) as create_progress_reporter,
            patch.object(main_module, "prepare_noise") as prepare_noise,
        ):
            create_progress_reporter.return_value.__enter__.return_value = (
                progress
            )
            main_module.main(
                ["noise", "sleep", "--chunk-duration", "120"]
            )

        prepare_noise.assert_called_once_with(
            package_root=package_root,
            noise_name="sleep",
            chunk_duration_seconds=120,
            progress=progress,
        )

    def test_legacy_source_invocation_accepts_remix(self):
        with patch.object(main_module, "submit_project") as submit_project:
            main_module.main(["BV123", "--remix", "sleep"])

        submit_project.assert_called_once()
        self.assertEqual(
            submit_project.call_args.kwargs["source_str"], "BV123"
        )
        self.assertEqual(
            submit_project.call_args.kwargs["remix_noise_name"], "sleep"
        )
        self.assertFalse(submit_project.call_args.kwargs["remix_prefix"])

    def test_legacy_source_invocation_accepts_remix_prefix(self):
        with patch.object(main_module, "submit_project") as submit_project:
            main_module.main(["BV123", "--remix", "sleep", "--prefix"])

        submit_project.assert_called_once()
        self.assertTrue(submit_project.call_args.kwargs["remix_prefix"])

    def test_prefix_without_remix_fails(self):
        with patch.object(main_module, "submit_project") as submit_project:
            main_module.main(["BV123", "--prefix"])

        submit_project.assert_not_called()

    def test_section_flags_are_parsed_to_seconds(self):
        with patch.object(main_module, "submit_project") as submit_project:
            main_module.main(["BV123", "--start", "1:30", "--to", "10:00"])

        submit_project.assert_called_once()
        self.assertEqual(
            submit_project.call_args.kwargs["section_start"], 90.0
        )
        self.assertEqual(
            submit_project.call_args.kwargs["section_end"], 600.0
        )

    def test_section_flags_default_to_none(self):
        with patch.object(main_module, "submit_project") as submit_project:
            main_module.main(["BV123"])

        submit_project.assert_called_once()
        self.assertIsNone(submit_project.call_args.kwargs["section_start"])
        self.assertIsNone(submit_project.call_args.kwargs["section_end"])

    def test_start_only_is_accepted(self):
        with patch.object(main_module, "submit_project") as submit_project:
            main_module.main(["BV123", "--start", "90"])

        submit_project.assert_called_once()
        self.assertEqual(
            submit_project.call_args.kwargs["section_start"], 90.0
        )
        self.assertIsNone(submit_project.call_args.kwargs["section_end"])

    def test_invalid_section_time_fails(self):
        with patch.object(main_module, "submit_project") as submit_project:
            main_module.main(["BV123", "--start", "abc"])

        submit_project.assert_not_called()

    def test_to_not_after_start_fails(self):
        with patch.object(main_module, "submit_project") as submit_project:
            main_module.main(["BV123", "--start", "10:00", "--to", "1:30"])

        submit_project.assert_not_called()


if __name__ == "__main__":
    unittest.main()
