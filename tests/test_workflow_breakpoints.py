import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import workflow as workflow_module
import workflow.api as workflow_api
import workflow.side_tasks as side_tasks
import workflow.stages.media as media_stage
import workflow.stages.metadata as metadata_stage
import workflow.stages.postprocess as postprocess_stage
import workflow.stages.transcription as transcription_stage
import workflow.stages.translation as translation_stage
import project as project_module
from project import Project
from services.elevenlabs.asr import ElevenLabsTranscriptionResult
from services.progress import NoopProgressReporter
from services.ytdlp.info import AbemaTalent, TVerTalent, YtDlpVideoInfo


class WorkflowBreakpointTests(unittest.TestCase):
    def _make_temp_dir(self) -> Path:
        base = Path(__file__).resolve().parents[1] / "tmp_test_artifacts"
        base.mkdir(parents=True, exist_ok=True)
        path = base / "tmp_workflow_breakpoints"
        import shutil

        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

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
        project.is_cover_generated = False
        project.is_broadcast_date_researched = False
        project.broadcast_date = None
        project.audio_path = Path("projects/demo/.asr/audio.ogg")
        project.asr_path = Path("projects/demo/.asr/asr.json")
        project.srt_path = Path("projects/demo/video.ja.srt")
        project.translated_path = Path("projects/demo/video.cht.srt")
        project.refined_srt_path = Path("projects/demo/video.cht.refined.srt")
        project.glossary_checked_srt_path = Path(
            "projects/demo/video.cht.glossary_checked.srt"
        )
        project.poster_path = Path("projects/demo/poster.jpg")
        project.poster_cover_path = Path("projects/demo/poster.cover.png")
        return project

    def _build_completed_project_mock(self):
        project = self._build_project_mock()
        for stage in workflow_module.ProgressStage:
            setattr(project, stage.value, True)
        project.is_cover_generated = True
        project.is_broadcast_date_researched = True
        return project

    def test_break_after_asr_completed_stops_before_translation(self):
        project = self._build_project_mock()

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
            asr = elevenlabs_cls.return_value
            asr.transcribe_to_file.return_value = ElevenLabsTranscriptionResult(
                audio_duration_secs=1800,
                total_cost=0.11,
            )

            workflow_module.process_project(
                "demo",
                break_after=workflow_module.ProgressStage.ASR_COMPLETED,
            )

        asr.transcribe_to_file.assert_called_once_with(
            project.audio_path, project.asr_path
        )
        convert_file.assert_not_called()
        project.mark_progress.assert_called_once_with(
            workflow_module.ProgressStage.ASR_COMPLETED
        )
        gemini_cls.assert_not_called()

    def test_break_after_completed_stage_stops_on_resumed_project(self):
        project = self._build_project_mock()
        project.is_asr_completed = True

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
            workflow_module.process_project(
                "demo",
                break_after=workflow_module.ProgressStage.ASR_COMPLETED,
            )

        elevenlabs_cls.assert_not_called()
        convert_file.assert_not_called()
        project.mark_progress.assert_not_called()
        gemini_cls.assert_not_called()

    def test_section_run_combines_to_full_video_then_cuts(self):
        root = self._make_temp_dir()
        project = self._build_project_mock()
        project.is_video_processed = False
        project.downloaded_video_paths = [root / "0.mp4"]
        project.full_video_path = root / "video.full.mp4"
        project.video_path = root / "video.mp4"

        with (
            patch.object(
                workflow_api.Project, "from_source_str", return_value=project
            ),
            patch.object(media_stage, "MediaProcessor") as media_cls,
        ):
            workflow_module.process_project(
                "demo",
                break_after=workflow_module.ProgressStage.VIDEO_PROCESSED,
                section_start=90.0,
                section_end=600.0,
            )

        media_cls.combine_videos.assert_called_once_with(
            project.downloaded_video_paths, project.full_video_path
        )
        media_cls.cut_video.assert_called_once_with(
            project.full_video_path,
            project.video_path,
            start_seconds=90.0,
            end_seconds=600.0,
        )

    def test_section_resume_skips_combine_when_full_video_exists(self):
        root = self._make_temp_dir()
        project = self._build_project_mock()
        project.is_video_processed = False
        project.downloaded_video_paths = []
        project.full_video_path = root / "video.full.mp4"
        project.full_video_path.touch()
        project.video_path = root / "video.mp4"

        with (
            patch.object(
                workflow_api.Project, "from_source_str", return_value=project
            ),
            patch.object(media_stage, "MediaProcessor") as media_cls,
        ):
            workflow_module.process_project(
                "demo",
                break_after=workflow_module.ProgressStage.VIDEO_PROCESSED,
                section_start=90.0,
            )

        media_cls.combine_videos.assert_not_called()
        media_cls.cut_video.assert_called_once_with(
            project.full_video_path,
            project.video_path,
            start_seconds=90.0,
            end_seconds=None,
        )

    def test_no_section_combines_directly_to_video(self):
        root = self._make_temp_dir()
        project = self._build_project_mock()
        project.is_video_processed = False
        project.downloaded_video_paths = [root / "0.mp4"]
        project.video_path = root / "video.mp4"

        with (
            patch.object(
                workflow_api.Project, "from_source_str", return_value=project
            ),
            patch.object(media_stage, "MediaProcessor") as media_cls,
        ):
            workflow_module.process_project(
                "demo",
                break_after=workflow_module.ProgressStage.VIDEO_PROCESSED,
            )

        media_cls.combine_videos.assert_called_once_with(
            project.downloaded_video_paths, project.video_path
        )
        media_cls.cut_video.assert_not_called()

    def test_break_after_prepass_completed_stops_before_chunk_translation(self):
        project = self._build_project_mock()
        project.is_asr_completed = True
        project.is_srt_completed = True
        project.translation_hint = None
        project.video_path = Path("projects/demo/video.mp4")
        project.pre_pass_path = Path("projects/demo/.pre_pass/pre_pass.json")
        project.pre_pass_cache_dir = Path("projects/demo/.pre_pass")
        project.chunks_cache_dir = Path("projects/demo/.chunks")
        project.official_subtitle_path = Path(
            "projects/demo/video.official.ja.srt"
        )
        project.source_metadata_context.return_value = None
        project.parent_pre_pass_context.return_value = None

        with (
            patch.object(
                workflow_api.Project, "from_source_str", return_value=project
            ),
            patch.object(translation_stage, "Translate") as gemini_cls,
        ):
            gemini = gemini_cls.return_value
            gemini.run_pre_pass.return_value = MagicMock(total_cost=0.0)

            workflow_module.process_project(
                "demo",
                break_after=workflow_module.ProgressStage.PREPASS_COMPLETED,
            )

        gemini.run_pre_pass.assert_called_once()
        gemini.translate_chunks.assert_not_called()
        project.mark_progress.assert_called_once_with(
            workflow_module.ProgressStage.PREPASS_COMPLETED
        )

    def test_successful_project_calls_delivery_with_remix_options(self):
        project = self._build_completed_project_mock()
        progress = NoopProgressReporter()

        with (
            patch.object(
                workflow_api.Project, "from_source_str", return_value=project
            ),
            patch.object(workflow_api, "deliver_project") as deliver_project,
        ):
            workflow_module.process_project(
                "demo",
                progress=progress,
                remix_noise_name="sleep",
                remix_prefix=True,
            )

        deliver_project.assert_called_once_with(
            project=project,
            project_id="demo",
            progress=progress,
            remix_noise_name="sleep",
            remix_prefix=True,
        )

    def test_optional_refine_runs_when_forced(self):
        project = self._build_completed_project_mock()
        project.is_srt_refined = False

        with (
            patch.object(
                workflow_api.Project, "from_source_str", return_value=project
            ),
            patch.object(
                postprocess_stage, "refine_project_subtitles"
            ) as refine,
            patch.object(workflow_api, "deliver_project"),
        ):
            workflow_module.process_project("demo", enable_refine=True)

        refine.assert_called_once_with(project)
        project.mark_progress.assert_called_once_with(
            workflow_module.ProgressStage.SRT_REFINED
        )

    def test_optional_refine_skips_when_disabled(self):
        project = self._build_completed_project_mock()
        project.is_srt_refined = False

        with (
            patch.object(
                workflow_api.Project, "from_source_str", return_value=project
            ),
            patch.object(
                postprocess_stage, "refine_project_subtitles"
            ) as refine,
            patch.object(workflow_api.settings, "enable_postprocess_refine", False),
            patch.object(workflow_api, "deliver_project"),
        ):
            workflow_module.process_project("demo")

        refine.assert_not_called()
        project.mark_progress.assert_not_called()

    def test_optional_glossary_check_runs_when_forced(self):
        project = self._build_completed_project_mock()
        project.is_glossary_checked = False

        with (
            patch.object(
                workflow_api.Project, "from_source_str", return_value=project
            ),
            patch.object(
                postprocess_stage, "glossary_check_project_subtitles"
            ) as glossary_check,
            patch.object(workflow_api, "deliver_project"),
        ):
            workflow_module.process_project(
                "demo", enable_glossary_check=True
            )

        glossary_check.assert_called_once_with(project)
        project.mark_progress.assert_called_once_with(
            workflow_module.ProgressStage.GLOSSARY_CHECKED
        )

    def test_metadata_stage_fetches_tver_talents(self):
        root = self._make_temp_dir()
        project_id = "epmetadata1"

        with (
            patch.object(project_module, "PROJECT_ROOT_NAME", str(root)),
            patch.object(
                metadata_stage,
                "get_video_info",
                return_value=YtDlpVideoInfo(
                    id=project_id,
                    title="かまいガチ",
                    description="episode description",
                ),
            ) as get_video_info,
            patch.object(
                metadata_stage,
                "get_tver_episode_talents",
                return_value=[
                    TVerTalent(
                        id="t001",
                        name="山内　健司",
                        name_kana="ヤマウチ　ケンジ",
                        roles=["お笑い芸人"],
                    )
                ],
            ) as get_tver_episode_talents,
            patch.object(
                metadata_stage,
                "resolve_broadcast_date",
                return_value=date(2026, 7, 8),
            ) as resolve_broadcast_date,
        ):
            workflow_module.process_project(
                project_id,
                break_after=workflow_module.ProgressStage.METADATA_FETCHED,
            )
            loaded = Project.from_source_str(project_id)

        get_video_info.assert_called_once_with(
            f"https://tver.jp/episodes/{project_id}"
        )
        get_tver_episode_talents.assert_called_once_with(project_id)
        resolve_broadcast_date.assert_called_once()
        self.assertTrue(loaded.is_metadata_fetched)
        self.assertEqual(
            loaded.source_metadata.talents[0].name,
            "山内　健司",
        )
        self.assertEqual(loaded.broadcast_date, date(2026, 7, 8))
        self.assertEqual(
            loaded.deliverable_name, f"260708_{project_id}_{loaded.name}"
        )

    def test_metadata_stage_fetches_abema_talents(self):
        root = self._make_temp_dir()
        project_id = "90-979_s1_p359"

        with (
            patch.object(project_module, "PROJECT_ROOT_NAME", str(root)),
            patch.object(
                metadata_stage,
                "get_video_info",
                return_value=YtDlpVideoInfo(
                    id=project_id,
                    title="チャンスの時間",
                    description="episode description",
                ),
            ) as get_video_info,
            patch.object(
                metadata_stage,
                "get_abema_episode_talents",
                return_value=[
                    AbemaTalent(
                        id=f"abema:{project_id}:1",
                        name="渡部健（アンジャッシュ）",
                        roles=["ゲスト"],
                    )
                ],
            ) as get_abema_episode_talents,
            patch.object(
                metadata_stage,
                "get_tver_episode_talents",
            ) as get_tver_episode_talents,
            patch.object(
                metadata_stage,
                "resolve_broadcast_date",
                return_value=None,
            ),
        ):
            workflow_module.process_project(
                project_id,
                break_after=workflow_module.ProgressStage.METADATA_FETCHED,
            )
            loaded = Project.from_source_str(project_id)

        get_video_info.assert_called_once_with(
            f"https://abema.tv/video/episode/{project_id}"
        )
        get_abema_episode_talents.assert_called_once_with(project_id)
        get_tver_episode_talents.assert_not_called()
        self.assertTrue(loaded.is_metadata_fetched)
        self.assertEqual(
            loaded.source_metadata.talents[0].name,
            "渡部健（アンジャッシュ）",
        )
        self.assertIsNone(loaded.broadcast_date)
        self.assertEqual(
            loaded.deliverable_name, f"{project_id}_{loaded.name}"
        )


if __name__ == "__main__":
    unittest.main()
