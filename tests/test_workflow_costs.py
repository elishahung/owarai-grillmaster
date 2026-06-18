import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import workflow as workflow_module
from services.elevenlabs.asr import ElevenLabsTranscriptionResult
from services.translate.errors import GeminiTranslationError, TranslationCostSummary


class WorkflowGeminiCostTests(unittest.TestCase):
    def _build_project_mock(self):
        project = MagicMock()
        project.id = "demo"
        project.translation_hint = "hint"
        project.total_cost = 0.0
        project.is_metadata_fetched = True
        project.is_downloaded = True
        project.is_video_processed = True
        project.is_audio_processed = True
        project.is_asr_completed = True
        project.is_srt_completed = True
        project.is_prepass_completed = True
        project.is_chunk_translated = False
        base = Path("projects/demo")
        project.srt_path = base / "video.ja.srt"
        project.video_path = base / "video.mp4"
        project.audio_path = base / ".asr" / "audio.ogg"
        project.translated_path = base / "video.cht.srt"
        project.pre_pass_path = base / ".pre_pass" / "pre_pass.json"
        project.pre_pass_cache_dir = base / ".pre_pass"
        project.chunks_cache_dir = base / ".chunks"
        project.source_metadata_context.return_value = None
        project.parent_pre_pass_context.return_value = None
        return project

    def test_workflow_persists_gemini_cost_on_success(self):
        project = self._build_project_mock()
        summary = TranslationCostSummary(
            total_cost=3.5,
            pre_pass_cost=1.0,
            chunk_costs=[1.0, 1.5],
            num_chunks=2,
            retries=1,
            elapsed_seconds=5.0,
            completed_chunks=2,
            failed_chunks=[],
        )

        with (
            patch.object(
                workflow_module.Project, "from_source_str", return_value=project
            ),
            patch.object(workflow_module, "Translate") as gemini_cls,
            patch.object(workflow_module.settings, "archived_path", None),
            patch.object(workflow_module.settings, "package_path", None),
        ):
            gemini_cls.return_value.translate_chunks.return_value = summary
            workflow_module.process_project("demo")

        project.add_cost.assert_called_once_with("gemini", 3.5)
        request = gemini_cls.return_value.translate_chunks.call_args.args[0]
        self.assertEqual(request.video_description, "hint")
        self.assertEqual(request.audio_key, "demo")
        self.assertEqual(request.srt_path, project.srt_path)
        project.mark_progress.assert_called_once_with(
            workflow_module.ProgressStage.CHUNK_TRANSLATED
        )

    def test_workflow_persists_partial_gemini_cost_on_failure(self):
        project = self._build_project_mock()
        summary = TranslationCostSummary(
            total_cost=2.25,
            pre_pass_cost=0.75,
            chunk_costs=[1.5, 0.0],
            num_chunks=2,
            retries=2,
            elapsed_seconds=4.0,
            completed_chunks=1,
            failed_chunks=["[chunk 2/2] index 11-20: failed"],
        )

        with (
            patch.object(
                workflow_module.Project, "from_source_str", return_value=project
            ),
            patch.object(workflow_module, "Translate") as gemini_cls,
            patch.object(workflow_module.settings, "archived_path", None),
        ):
            gemini_cls.return_value.translate_chunks.side_effect = (
                GeminiTranslationError("translation failed", summary)
            )
            with self.assertRaises(GeminiTranslationError):
                workflow_module.process_project("demo")

        project.add_cost.assert_called_once_with("gemini", 2.25)
        project.mark_progress.assert_not_called()

    def test_workflow_persists_prepass_cost_and_stops_at_break(self):
        project = self._build_project_mock()
        project.is_prepass_completed = False
        summary = TranslationCostSummary(
            total_cost=1.0,
            pre_pass_cost=1.0,
            chunk_costs=[],
            num_chunks=3,
            retries=0,
            elapsed_seconds=2.0,
            completed_chunks=0,
            failed_chunks=[],
        )

        with (
            patch.object(
                workflow_module.Project, "from_source_str", return_value=project
            ),
            patch.object(workflow_module, "Translate") as gemini_cls,
        ):
            gemini = gemini_cls.return_value
            gemini.run_pre_pass.return_value = summary
            workflow_module.process_project(
                "demo",
                break_after=workflow_module.ProgressStage.PREPASS_COMPLETED,
            )

        project.add_cost.assert_called_once_with("gemini", 1.0)
        gemini.translate_chunks.assert_not_called()
        project.mark_progress.assert_called_once_with(
            workflow_module.ProgressStage.PREPASS_COMPLETED
        )


class WorkflowElevenLabsCostTests(unittest.TestCase):
    def _build_project_mock(self):
        project = MagicMock()
        project.id = "demo"
        project.total_cost = 0.0
        project.is_metadata_fetched = True
        project.is_downloaded = True
        project.is_video_processed = True
        project.is_audio_processed = True
        project.is_asr_completed = False
        project.is_srt_completed = False
        project.is_prepass_completed = False
        project.is_chunk_translated = False
        base = Path("projects/demo")
        project.audio_path = base / ".asr" / "audio.ogg"
        project.asr_path = base / ".asr" / "asr.json"
        project.srt_path = base / "video.ja.srt"
        return project

    def test_workflow_persists_elevenlabs_cost_on_asr_success(self):
        project = self._build_project_mock()
        result = ElevenLabsTranscriptionResult(
            audio_duration_secs=1800,
            total_cost=0.11,
        )

        with (
            patch.object(
                workflow_module.Project, "from_source_str", return_value=project
            ),
            patch.object(workflow_module, "ElevenLabsASR") as elevenlabs_cls,
            patch.object(workflow_module, "convert_file") as convert_file,
            patch.object(workflow_module, "Translate") as gemini_cls,
        ):
            elevenlabs_cls.return_value.transcribe_to_file.return_value = result
            workflow_module.process_project(
                "demo",
                break_after=workflow_module.ProgressStage.ASR_COMPLETED,
            )

        project.add_cost.assert_called_once_with("elevenlabs", 0.11)
        project.mark_progress.assert_called_once_with(
            workflow_module.ProgressStage.ASR_COMPLETED
        )
        convert_file.assert_not_called()
        gemini_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
