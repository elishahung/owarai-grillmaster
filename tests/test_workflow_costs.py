import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import workflow as workflow_module
import workflow.api as workflow_api
import workflow.stages.transcription as transcription_stage
import workflow.stages.translation as translation_stage
from services.elevenlabs.asr import ElevenLabsTranscriptionResult
from services.translate.errors import TranslationCostSummary, TranslationError


class WorkflowGeminiCostTests(unittest.TestCase):
    def _build_project_mock(self):
        project = MagicMock()
        project.id = "demo"
        project.translation_hint = "hint"
        project.total_cost = 0.0
        for stage in workflow_module.ProgressStage:
            setattr(project, stage.value, False)
        project.is_metadata_fetched = True
        project.is_downloaded = True
        project.is_video_processed = True
        project.is_audio_processed = True
        project.is_asr_completed = True
        project.is_srt_completed = True
        project.is_prepass_completed = True
        project.is_srt_refined = True
        project.is_glossary_checked = True
        project.is_finalized = True
        project.is_cover_generated = True
        project.is_broadcast_date_researched = True
        project.broadcast_date = None
        base = Path("projects/demo")
        project.srt_path = base / "video.ja.srt"
        project.video_path = base / "video.mp4"
        project.audio_path = base / ".asr" / "audio.ogg"
        project.translated_path = base / "video.cht.srt"
        project.pre_pass_path = base / ".pre_pass" / "pre_pass.json"
        project.pre_pass_cache_dir = base / ".pre_pass"
        project.chunks_cache_dir = base / ".chunks"
        project.official_subtitle_path = base / "video.official.ja.srt"
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
                workflow_api.Project, "from_source_str", return_value=project
            ),
            patch.object(translation_stage, "Translate") as gemini_cls,
            patch.object(workflow_api.settings, "archived_path", None),
            patch.object(workflow_api.settings, "package_path", None),
        ):
            gemini_cls.return_value.translate_chunks.return_value = summary
            workflow_module.process_project("demo")

        project.add_cost.assert_called_once_with("gemini", 3.5)
        request = gemini_cls.return_value.translate_chunks.call_args.args[0]
        self.assertEqual(request.video_description, "hint")
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
                workflow_api.Project, "from_source_str", return_value=project
            ),
            patch.object(translation_stage, "Translate") as gemini_cls,
            patch.object(workflow_api.settings, "archived_path", None),
        ):
            gemini_cls.return_value.translate_chunks.side_effect = (
                TranslationError("translation failed", summary)
            )
            with self.assertRaises(TranslationError):
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
                workflow_api.Project, "from_source_str", return_value=project
            ),
            patch.object(translation_stage, "Translate") as gemini_cls,
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
        for stage in workflow_module.ProgressStage:
            setattr(project, stage.value, False)
        project.is_metadata_fetched = True
        project.is_downloaded = True
        project.is_video_processed = True
        project.is_audio_processed = True
        project.is_cover_generated = True
        project.is_broadcast_date_researched = True
        project.broadcast_date = None
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
                workflow_api.Project, "from_source_str", return_value=project
            ),
            patch.object(
                transcription_stage, "ElevenLabsASR"
            ) as elevenlabs_cls,
            patch.object(transcription_stage, "convert_file") as convert_file,
            patch.object(translation_stage, "Translate") as gemini_cls,
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
