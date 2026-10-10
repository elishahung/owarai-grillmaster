import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import grillmaster.workflow as workflow_module
import grillmaster.workflow.api as workflow_api
import grillmaster.workflow.stages.transcription as transcription_stage
import grillmaster.workflow.stages.translation as translation_stage
from grillmaster.project import SourceMetadata
from grillmaster.services.program_config import ProgramRules
from grillmaster.services.elevenlabs.asr import ElevenLabsTranscriptionResult
from grillmaster.services.translate.errors import TranslationError


class WorkflowTranslationStageTests(unittest.TestCase):
    def _build_project_mock(self):
        project = MagicMock()
        project.id = "demo"
        project.translation_hint = "hint"
        project.asr_cost = 0.0
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
        project.source_metadata = SourceMetadata(title="title")
        project.program_rules.return_value = ProgramRules()
        project.source_metadata_context.return_value = None
        project.parent_pre_pass_context.return_value = None
        return project

    def test_chunk_stage_builds_request_and_marks_progress(self):
        project = self._build_project_mock()

        with (
            patch.object(workflow_api.Project, "from_source_str", return_value=project),
            patch.object(translation_stage, "translate") as translate_mod,
            patch.object(workflow_api.settings, "archived_path", None),
            patch.object(workflow_api.settings, "package_path", None),
        ):
            workflow_module.process_project("demo")

        # Every model backend is a subscription agent: no model cost is recorded.
        project.add_asr_cost.assert_not_called()
        request = translate_mod.translate_chunks.call_args.args[0]
        self.assertEqual(request.video_title, "title")
        self.assertEqual(request.translation_hint, "hint")
        self.assertEqual(request.srt_path, project.srt_path)
        project.mark_progress.assert_called_once_with(
            workflow_module.ProgressStage.CHUNK_TRANSLATED
        )

    def test_chunk_stage_failure_does_not_mark_progress(self):
        project = self._build_project_mock()

        with (
            patch.object(workflow_api.Project, "from_source_str", return_value=project),
            patch.object(translation_stage, "translate") as translate_mod,
            patch.object(workflow_api.settings, "archived_path", None),
        ):
            translate_mod.translate_chunks.side_effect = TranslationError(
                "1/2 chunks failed"
            )
            with self.assertRaises(TranslationError):
                workflow_module.process_project("demo")

        project.add_asr_cost.assert_not_called()
        project.mark_progress.assert_not_called()

    def test_prepass_stage_stops_at_break(self):
        project = self._build_project_mock()
        project.is_prepass_completed = False

        with (
            patch.object(workflow_api.Project, "from_source_str", return_value=project),
            patch.object(translation_stage, "translate") as translate_mod,
        ):
            workflow_module.process_project(
                "demo",
                break_after=workflow_module.ProgressStage.PREPASS_COMPLETED,
            )

        translate_mod.run_pre_pass.assert_called_once()
        translate_mod.translate_chunks.assert_not_called()
        project.mark_progress.assert_called_once_with(
            workflow_module.ProgressStage.PREPASS_COMPLETED
        )


class WorkflowAsrCostTests(unittest.TestCase):
    def _build_project_mock(self):
        project = MagicMock()
        project.id = "demo"
        project.asr_cost = 0.0
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
            patch.object(workflow_api.Project, "from_source_str", return_value=project),
            patch.object(transcription_stage, "ElevenLabsASR") as elevenlabs_cls,
            patch.object(transcription_stage, "convert_file") as convert_file,
            patch.object(translation_stage, "translate") as translate_mod,
        ):
            elevenlabs_cls.return_value.transcribe_to_file.return_value = result
            workflow_module.process_project(
                "demo",
                break_after=workflow_module.ProgressStage.ASR_COMPLETED,
            )

        project.add_asr_cost.assert_called_once_with(0.11)
        project.mark_progress.assert_called_once_with(
            workflow_module.ProgressStage.ASR_COMPLETED
        )
        convert_file.assert_not_called()
        translate_mod.run_pre_pass.assert_not_called()


if __name__ == "__main__":
    unittest.main()
