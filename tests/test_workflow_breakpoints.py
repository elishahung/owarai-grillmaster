import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import workflow as workflow_module
import project as project_module
from project import Project
from services.elevenlabs.asr import ElevenLabsTranscriptionResult
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
        project.is_metadata_fetched = True
        project.is_downloaded = True
        project.is_video_processed = True
        project.is_audio_processed = True
        project.is_asr_completed = False
        project.is_srt_completed = False
        project.is_prepass_completed = False
        project.is_chunk_translated = False
        project.is_srt_refined = False
        project.is_glossary_checked = False
        project.is_cover_generated = False
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

    def test_break_after_asr_completed_stops_before_translation(self):
        project = self._build_project_mock()

        with (
            patch.object(
                workflow_module.Project, "from_source_str", return_value=project
            ),
            patch.object(workflow_module, "ElevenLabsASR") as elevenlabs_cls,
            patch.object(workflow_module, "convert_file") as convert_file,
            patch.object(workflow_module, "Translate") as gemini_cls,
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
                workflow_module.Project, "from_source_str", return_value=project
            ),
            patch.object(workflow_module, "ElevenLabsASR") as elevenlabs_cls,
            patch.object(workflow_module, "convert_file") as convert_file,
            patch.object(workflow_module, "Translate") as gemini_cls,
        ):
            workflow_module.process_project(
                "demo",
                break_after=workflow_module.ProgressStage.ASR_COMPLETED,
            )

        elevenlabs_cls.assert_not_called()
        convert_file.assert_not_called()
        project.mark_progress.assert_not_called()
        gemini_cls.assert_not_called()

    def test_break_after_prepass_completed_stops_before_chunk_translation(self):
        project = self._build_project_mock()
        project.is_asr_completed = True
        project.is_srt_completed = True
        project.translation_hint = None
        project.video_path = Path("projects/demo/video.mp4")
        project.pre_pass_path = Path("projects/demo/.pre_pass/pre_pass.json")
        project.pre_pass_cache_dir = Path("projects/demo/.pre_pass")
        project.chunks_cache_dir = Path("projects/demo/.chunks")
        project.source_metadata_context.return_value = None
        project.parent_pre_pass_context.return_value = None

        with (
            patch.object(
                workflow_module.Project, "from_source_str", return_value=project
            ),
            patch.object(workflow_module, "Translate") as gemini_cls,
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

    def test_metadata_stage_fetches_tver_talents(self):
        root = self._make_temp_dir()
        project_id = "epmetadata1"

        with (
            patch.object(project_module, "PROJECT_ROOT_NAME", str(root)),
            patch.object(
                workflow_module,
                "get_video_info",
                return_value=YtDlpVideoInfo(
                    id=project_id,
                    title="かまいガチ",
                    description="episode description",
                ),
            ) as get_video_info,
            patch.object(
                workflow_module,
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
        self.assertTrue(loaded.is_metadata_fetched)
        self.assertEqual(
            loaded.source_metadata.talents[0].name,
            "山内　健司",
        )

    def test_metadata_stage_fetches_abema_talents(self):
        root = self._make_temp_dir()
        project_id = "90-979_s1_p359"

        with (
            patch.object(project_module, "PROJECT_ROOT_NAME", str(root)),
            patch.object(
                workflow_module,
                "get_video_info",
                return_value=YtDlpVideoInfo(
                    id=project_id,
                    title="チャンスの時間",
                    description="episode description",
                ),
            ) as get_video_info,
            patch.object(
                workflow_module,
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
                workflow_module,
                "get_tver_episode_talents",
            ) as get_tver_episode_talents,
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


if __name__ == "__main__":
    unittest.main()
