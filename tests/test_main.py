import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main as main_module
from services.live_chat import ChatLayout


class MainCliTests(unittest.TestCase):
    def _make_temp_dir(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="main-cli-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    def test_process_uses_dashboard_on_interactive_terminal(self):
        with (
            patch.object(
                main_module, "_is_interactive_terminal", return_value=True
            ),
            patch("services.tui.run_process_ui", return_value=0) as run_ui,
            patch.object(main_module, "submit_project") as submit_project,
        ):
            main_module.main(["process", "demo"])

            run_ui.assert_called_once()
            # The dashboard drives submit_project itself via the callback;
            # invoke it while the patch is active.
            submit_project.assert_not_called()
            pipeline = run_ui.call_args.args[0]
            reporter = object()
            pipeline(reporter)
            self.assertIs(
                submit_project.call_args.kwargs["progress"], reporter
            )
            self.assertEqual(
                submit_project.call_args.kwargs["source_str"], "demo"
            )

    def test_process_falls_back_to_plain_logs_without_terminal(self):
        with (
            patch.object(
                main_module, "_is_interactive_terminal", return_value=False
            ),
            patch("services.tui.run_process_ui") as run_ui,
            patch.object(main_module, "submit_project") as submit_project,
        ):
            main_module.main(["process", "demo"])

        run_ui.assert_not_called()
        submit_project.assert_called_once()
        # No reporter is injected: submit_project builds its own plain one.
        self.assertIsNone(submit_project.call_args.kwargs["progress"])

    def test_serial_drives_serial_run_under_dashboard(self):
        with (
            patch.object(
                main_module, "_is_interactive_terminal", return_value=True
            ),
            patch("services.tui.run_process_ui", return_value=0) as run_ui,
            patch.object(main_module, "SerialRun") as serial_run_cls,
        ):
            main_module.main(
                ["serial", "BV1", "BV2", "--refine", "--remix",
                 "--parent-project", "archived/ep0"]
            )

        serial_run_cls.assert_called_once_with(
            sources=["BV1", "BV2"],
            parent_project_path=Path("archived/ep0"),
            submit_kwargs=dict(
                enable_refine=True,
                enable_glossary_check=False,
                enable_cover=False,
                enable_date_research=False,
                enable_live_chat=False,
                chat_layout=ChatLayout.SIDE,
                remix_noise_name=main_module.DEFAULT_NOISE_NAME,
            ),
        )
        run_ui.assert_called_once_with(serial_run_cls.return_value.run)

    def test_serial_runs_plainly_without_terminal(self):
        with (
            patch.object(
                main_module, "_is_interactive_terminal", return_value=False
            ),
            patch("services.tui.run_process_ui") as run_ui,
            patch.object(main_module, "SerialRun") as serial_run_cls,
        ):
            main_module.main(["serial", "BV1", "BV2"])

        run_ui.assert_not_called()
        serial_run_cls.return_value.run.assert_called_once_with(None)

    def test_serial_rejects_invalid_sources_before_running(self):
        with (
            patch.object(
                main_module, "_is_interactive_terminal", return_value=False
            ),
            patch.object(main_module, "_run_pipeline") as run_pipeline,
        ):
            # main() runs typer with standalone_mode=False, which turns the
            # typer.Exit(1) into a swallowed return code.
            main_module.main(["serial", "BV1", "BV1"])

        run_pipeline.assert_not_called()

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
                ["package", str(project_dir), "--remix", "sleep", "--skip-chat"]
            )

        package_project_directory.assert_called_once_with(
            project_dir=project_dir,
            package_root=package_root,
            remix_noise_name="sleep",
            progress=progress,
            chat_layout=ChatLayout.NONE,
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

    def test_chat_layout_option_reaches_submit(self):
        with patch.object(main_module, "submit_project") as submit_project:
            main_module.main(["BV123", "--chat", "--chat-layout", "Overlay"])

        kwargs = submit_project.call_args.kwargs
        self.assertTrue(kwargs["enable_live_chat"])
        self.assertEqual(kwargs["chat_layout"], ChatLayout.OVERLAY)

    def test_package_rejects_skip_chat_with_another_layout(self):
        with (
            patch.object(main_module.settings, "package_path", Path("package")),
            patch.object(main_module, "package_project_directory") as package,
        ):
            # standalone_mode=False swallows the typer.Exit(1).
            main_module.main(
                ["package", "proj", "--skip-chat", "--chat-layout", "overlay"]
            )
        package.assert_not_called()

    def test_valueless_remix_uses_the_default_noise_set(self):
        with patch.object(main_module, "submit_project") as submit_project:
            main_module.main(["BV123", "--remix"])

        self.assertEqual(
            submit_project.call_args.kwargs["remix_noise_name"], "default"
        )

    def test_valueless_remix_before_another_flag(self):
        with patch.object(main_module, "submit_project") as submit_project:
            main_module.main(["BV123", "--remix", "--cover"])

        self.assertEqual(
            submit_project.call_args.kwargs["remix_noise_name"], "default"
        )

    def test_package_command_accepts_valueless_remix(self):
        root = self._make_temp_dir()
        project_dir = root / "project"
        project_dir.mkdir()

        with (
            patch.object(
                main_module.settings, "package_path", root / "package"
            ),
            patch.object(
                main_module, "package_project_directory"
            ) as package_project_directory,
        ):
            main_module.main(["package", str(project_dir), "--remix"])

        self.assertEqual(
            package_project_directory.call_args.kwargs["remix_noise_name"],
            "default",
        )

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
