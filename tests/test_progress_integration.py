import asyncio
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import workflow as workflow_module
import workflow.api as workflow_api
import workflow.stages.translation as translation_stage
from services.translate.chunk.chunk_worker import ChunkTranslationResult
from services.translate.errors import (
    ChunkTranslationError,
    TranslationCostSummary,
    TranslationError,
)
from services.translate.facade import Translate, TranslationRequest
from services.translate.pre_pass.pre_pass import PrePassResult
from services.media import (
    PACKAGE_LEAD_TRIM_SECONDS,
    MediaProcessor,
    NoiseCut,
    package_output_duration,
    package_usable_duration,
)
from services.progress import NoopProgressReporter, RichProgressReporter
from services.srt import SrtBlock, parse_srt
from rich.console import Console


class FakeProgressReporter(NoopProgressReporter):
    def __init__(self):
        self.events = []
        self._next_task = 1
        self.suspended = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def start_stage(self, label: str, total: float | None = None):
        task_id = self._next_task
        self._next_task += 1
        self.events.append(("start_stage", task_id, label, total))
        return task_id

    def advance(
        self, task_id, amount: float = 1.0, description: str | None = None
    ):
        self.events.append(("advance", task_id, amount, description))

    def finish(self, task_id, status: str = "done"):
        self.events.append(("finish", task_id, status))

    def suspend(self):
        reporter = self

        class SuspendContext:
            def __enter__(self):
                reporter.suspended += 1
                reporter.events.append(("suspend_enter",))

            def __exit__(self, exc_type, exc, traceback):
                reporter.events.append(("suspend_exit",))

        return SuspendContext()

    def chunk_started(
        self, index: int, total: int, from_index: int, to_index: int
    ):
        self.events.append(
            ("chunk_started", index, total, from_index, to_index)
        )

    def chunk_finished(self, index: int, retries: int, cost: float):
        self.events.append(("chunk_finished", index, retries, cost))

    def chunk_failed(
        self, index: int, message: str, retries: int = 0, cost: float = 0.0
    ):
        self.events.append(("chunk_failed", index, message, retries, cost))

    def pipeline_started(self, project, plan):
        self.events.append(
            ("pipeline_started", [entry.key for entry in plan])
        )

    def stage_started(self, key: str, label: str):
        self.events.append(("stage_started", key))

    def stage_completed(self, key, elapsed_seconds, result=None):
        self.events.append(("stage_completed", key))

    def stage_skipped(self, key: str, reason: str):
        self.events.append(("stage_skipped", key, reason))

    def side_task_started(self, key: str, label: str):
        self.events.append(("side_task_started", key))

    def side_task_completed(self, key, elapsed_seconds, result=None):
        self.events.append(("side_task_completed", key))

    def side_task_failed(self, key: str, message: str):
        self.events.append(("side_task_failed", key))

    def side_task_skipped(self, key: str, reason: str):
        self.events.append(("side_task_skipped", key, reason))

    def pipeline_completed(self):
        self.events.append(("pipeline_completed",))

    def pipeline_failed(self, message: str):
        self.events.append(("pipeline_failed", message))


class WorkflowProgressTests(unittest.TestCase):
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
        project.is_cover_generated = False
        project.is_broadcast_date_researched = True
        project.broadcast_date = None
        project.is_finalized = True
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

    def test_workflow_passes_progress_to_chunk_translation(self):
        project = self._build_project_mock()
        progress = FakeProgressReporter()
        summary = TranslationCostSummary(
            total_cost=0.5,
            pre_pass_cost=0.0,
            chunk_costs=[0.5],
            num_chunks=1,
            retries=0,
            elapsed_seconds=1.0,
            completed_chunks=1,
            failed_chunks=[],
        )

        with (
            patch.object(
                workflow_api.Project, "from_source_str", return_value=project
            ),
            patch.object(translation_stage, "Translate") as gemini_cls,
            patch.object(workflow_api.settings, "archived_path", None),
            patch.object(workflow_api.settings, "package_path", None),
            # Pin the optional-stage toggles so the expected event list does
            # not depend on the local .env.
            patch.object(
                workflow_api.settings, "enable_postprocess_refine", False
            ),
            patch.object(
                workflow_api.settings,
                "enable_postprocess_glossary_check",
                False,
            ),
            patch.object(
                workflow_api.settings, "enable_cover_generation", False
            ),
            patch.object(
                workflow_api.settings,
                "enable_broadcast_date_agent_fallback",
                False,
            ),
        ):
            gemini_cls.return_value.translate_chunks.return_value = summary
            workflow_module.process_project("demo", progress=progress)

        gemini_cls.return_value.translate_chunks.assert_called_once()
        self.assertIs(
            gemini_cls.return_value.translate_chunks.call_args.kwargs[
                "progress"
            ],
            progress,
        )
        # All stages except chunk translation are cached/disabled on this
        # project mock, so the reporter sees only lifecycle events — no
        # start_stage/advance bars.
        self.assertEqual(
            progress.events,
            [
                (
                    "pipeline_started",
                    [
                        "metadata",
                        "download",
                        "combine",
                        "audio",
                        "asr",
                        "srt",
                        "prepass",
                        "chunks",
                        "refine",
                        "glossary",
                        "finalize",
                        "date",
                        "cover",
                    ],
                ),
                ("stage_skipped", "metadata", "already-complete"),
                ("side_task_skipped", "date", "disabled"),
                ("stage_skipped", "download", "already-complete"),
                ("side_task_skipped", "cover", "disabled"),
                ("stage_skipped", "combine", "already-complete"),
                ("stage_skipped", "audio", "already-complete"),
                ("stage_skipped", "asr", "already-complete"),
                ("stage_skipped", "srt", "already-complete"),
                ("stage_skipped", "prepass", "already-complete"),
                ("stage_started", "chunks"),
                ("stage_completed", "chunks"),
                ("stage_skipped", "refine", "disabled"),
                ("stage_skipped", "glossary", "disabled"),
                ("stage_skipped", "finalize", "already-complete"),
                ("pipeline_completed",),
            ],
        )


class GeminiProgressTests(unittest.TestCase):
    def _make_request(self):
        root = Path(tempfile.mkdtemp(prefix="gemini-progress-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        srt_path = root / "source.srt"
        srt_path.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nOne\n\n"
            "2\n00:00:01,000 --> 00:00:02,000\nTwo\n",
            encoding="utf-8",
        )
        pre_pass = PrePassResult(
            summary="summary",
            characters=[],
            proper_nouns={},
            glossary={},
            catchphrases=[],
            tone_notes="",
            segment_summaries=[],
        )
        pre_pass_path = root / "pre_pass.json"
        pre_pass_path.write_text(
            pre_pass.model_dump_json(), encoding="utf-8"
        )
        request = TranslationRequest(
            video_description=None,
            srt_path=srt_path,
            video_path=root / "video.mp4",
            audio_path=root / "audio.ogg",
            output_path=root / "translated.srt",
            pre_pass_path=pre_pass_path,
            pre_pass_cache_dir=root / ".pre_pass",
            chunks_cache_dir=root / ".chunks",
        )
        return request, parse_srt(srt_path.read_text(encoding="utf-8"))

    def test_gemini_reports_chunk_completion_and_preserves_order(self):
        request, blocks = self._make_request()
        progress = FakeProgressReporter()
        gemini = Translate.__new__(Translate)
        gemini._client = object()

        async def fake_translate(
            media_assets,
            chunk,
            chunk_index,
            total_chunks,
            pre_pass,
            official_subtitle_blocks=None,
        ):
            if chunk_index == 0:
                await asyncio.sleep(0.01)
            return ChunkTranslationResult(
                blocks=chunk,
                cost=chunk_index + 0.5,
                retries=chunk_index,
                from_index=chunk[0].index,
                to_index=chunk[-1].index,
            )

        with (
            patch(
                "services.translate.facade.split_into_chunks",
                return_value=[[blocks[0]], [blocks[1]]],
            ),
            patch(
                "services.translate.facade.prepare_chunk_media_assets",
                return_value=MagicMock(),
            ),
            patch(
                "services.translate.facade.translate_chunk",
                side_effect=fake_translate,
            ),
        ):
            result = asyncio.run(
                gemini._translate_chunks_async(request, progress)
            )

        self.assertEqual(result.completed_chunks, 2)
        self.assertEqual(
            request.output_path.read_text(encoding="utf-8"),
            "1\n00:00:00,000 --> 00:00:01,000\nOne\n\n"
            "2\n00:00:01,000 --> 00:00:02,000\nTwo\n",
        )
        self.assertEqual(
            [event[0] for event in progress.events].count("chunk_finished"),
            2,
        )

    def test_gemini_reports_chunk_failure(self):
        request, blocks = self._make_request()
        progress = FakeProgressReporter()
        gemini = Translate.__new__(Translate)
        gemini._client = object()

        async def fake_translate(
            media_assets,
            chunk,
            chunk_index,
            total_chunks,
            pre_pass,
            official_subtitle_blocks=None,
        ):
            if chunk_index == 1:
                raise ChunkTranslationError(
                    "failed",
                    accumulated_cost=1.25,
                    retries=2,
                    chunk_index=chunk_index,
                    total_chunks=total_chunks,
                    from_index=chunk[0].index,
                    to_index=chunk[-1].index,
                )
            return ChunkTranslationResult(
                blocks=chunk,
                cost=0.5,
                retries=0,
                from_index=chunk[0].index,
                to_index=chunk[-1].index,
            )

        with (
            patch(
                "services.translate.facade.split_into_chunks",
                return_value=[[blocks[0]], [blocks[1]]],
            ),
            patch(
                "services.translate.facade.prepare_chunk_media_assets",
                return_value=MagicMock(),
            ),
            patch(
                "services.translate.facade.translate_chunk",
                side_effect=fake_translate,
            ),
        ):
            with self.assertRaises(TranslationError):
                asyncio.run(gemini._translate_chunks_async(request, progress))

        self.assertTrue(
            any(event[0] == "chunk_failed" for event in progress.events)
        )


class MediaProgressTests(unittest.TestCase):
    def test_burn_in_subtitles_reports_ffmpeg_progress(self):
        root = Path(tempfile.mkdtemp(prefix="burn-progress-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        video = root / "video.mp4"
        subtitle = root / "video.ass"
        output = root / "out.mp4"
        video.write_text("video", encoding="utf-8")
        subtitle.write_text("subtitle", encoding="utf-8")
        progress = FakeProgressReporter()

        class FakeProcess:
            stdout = iter(
                [
                    "out_time_ms=500000\n",
                    "out_time_ms=1000000\n",
                    "progress=end\n",
                ]
            )
            stderr = iter([])

            def wait(self):
                return 0

        source_duration = 10.0
        expected_duration = package_output_duration(
            package_usable_duration(source_duration)
        )
        with (
            patch.object(
                MediaProcessor,
                "get_media_duration",
                side_effect=[source_duration, expected_duration],
            ),
            patch("services.media.subprocess.Popen", return_value=FakeProcess()) as popen,
        ):
            MediaProcessor.burn_in_subtitles(
                video, subtitle, output, progress=progress
            )

        cmd = popen.call_args.args[0]
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("-nostdin", cmd)
        self.assertIn("subtitles=video.ass,", filter_complex)
        self.assertIn(
            f"trim=start={PACKAGE_LEAD_TRIM_SECONDS}:duration=7.000",
            filter_complex,
        )
        self.assertIn(
            f"atrim=start={PACKAGE_LEAD_TRIM_SECONDS}:duration=7.000",
            filter_complex,
        )
        self.assertIn(MediaProcessor._PACKAGE_VIDEO_FILTER, filter_complex)
        self.assertIn("rotw(", filter_complex)
        self.assertIn("roth(", filter_complex)
        self.assertNotIn("rotw(a)", filter_complex)
        self.assertIn(MediaProcessor._PACKAGE_AUDIO_FILTER, filter_complex)
        self.assertIn("anoisesrc=", filter_complex)
        self.assertIn("a=0.008", filter_complex)
        self.assertIn("amix=inputs=2", filter_complex)
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "h264_nvenc")
        self.assertNotIn("copy", cmd[cmd.index("-c:a") + 1])
        self.assertIn(
            (
                "start_stage",
                1,
                "Burning subtitles",
                expected_duration,
            ),
            progress.events,
        )
        self.assertIn(("advance", 1, 0.5, None), progress.events)
        self.assertEqual(progress.events[-1], ("finish", 1, "done"))

    def test_burn_in_subtitles_collects_stderr_on_failure(self):
        root = Path(tempfile.mkdtemp(prefix="burn-progress-fail-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        video = root / "video.mp4"
        subtitle = root / "video.ass"
        output = root / "out.mp4"
        video.write_text("video", encoding="utf-8")
        subtitle.write_text("subtitle", encoding="utf-8")
        progress = FakeProgressReporter()

        class FakeProcess:
            stdout = iter([])
            stderr = iter(["bad filter\n"])

            def wait(self):
                return 1

        with (
            patch.object(MediaProcessor, "get_media_duration", return_value=10.0),
            patch("services.media.subprocess.Popen", return_value=FakeProcess()),
        ):
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                MediaProcessor.burn_in_subtitles(
                    video,
                    subtitle,
                    output,
                    progress=progress,
                )

        self.assertIn("bad filter", raised.exception.stderr)
        self.assertEqual(progress.events[-1], ("finish", 1, "failed"))

    def test_burn_in_subtitles_rejects_video_shorter_than_lead_trim(self):
        root = Path(tempfile.mkdtemp(prefix="burn-lead-trim-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        video = root / "video.mp4"
        subtitle = root / "video.ass"
        output = root / "out.mp4"
        video.write_text("video", encoding="utf-8")
        subtitle.write_text("subtitle", encoding="utf-8")

        with (
            patch.object(MediaProcessor, "get_media_duration", return_value=2.0),
            patch("services.media.subprocess.Popen") as popen,
        ):
            with self.assertRaisesRegex(ValueError, "lead trim"):
                MediaProcessor.burn_in_subtitles(video, subtitle, output)

        popen.assert_not_called()

    def test_burn_in_subtitles_rejects_short_successful_output(self):
        root = Path(tempfile.mkdtemp(prefix="burn-progress-short-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        video = root / "video.mp4"
        subtitle = root / "video.ass"
        output = root / "out.mp4"
        video.write_text("video", encoding="utf-8")
        subtitle.write_text("subtitle", encoding="utf-8")
        progress = FakeProgressReporter()

        class FakeProcess:
            stdout = iter(["out_time_ms=5000000\n", "progress=end\n"])
            stderr = iter([])

            def wait(self):
                return 0

        with (
            patch.object(
                MediaProcessor,
                "get_media_duration",
                side_effect=[20.0, 5.0],
            ),
            patch("services.media.subprocess.Popen", return_value=FakeProcess()),
        ):
            with self.assertRaisesRegex(ValueError, "differs from expected"):
                MediaProcessor.burn_in_subtitles(
                    video,
                    subtitle,
                    output,
                    progress=progress,
                )

        self.assertEqual(progress.events[-1], ("finish", 1, "failed"))

    def test_burn_in_subtitles_rejects_output_that_skips_tempo(self):
        root = Path(tempfile.mkdtemp(prefix="burn-progress-tempo-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        video = root / "video.mp4"
        subtitle = root / "video.ass"
        output = root / "out.mp4"
        video.write_text("video", encoding="utf-8")
        subtitle.write_text("subtitle", encoding="utf-8")

        class FakeProcess:
            stdout = iter(["progress=end\n"])
            stderr = iter([])

            def wait(self):
                return 0

        with (
            patch.object(
                MediaProcessor,
                "get_media_duration",
                side_effect=[400.0, 400.0],
            ),
            patch("services.media.subprocess.Popen", return_value=FakeProcess()),
        ):
            with self.assertRaisesRegex(ValueError, "differs from expected"):
                MediaProcessor.burn_in_subtitles(video, subtitle, output)

    def test_remix_segment_reports_progress_to_existing_task(self):
        root = Path(tempfile.mkdtemp(prefix="remix-progress-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        video = root / "video.mp4"
        subtitle = root / "video.ass"
        output = root / "segment.mp4"
        video.write_text("video", encoding="utf-8")
        subtitle.write_text("subtitle", encoding="utf-8")
        progress = FakeProgressReporter()

        class FakeProcess:
            stdout = iter(
                [
                    "out_time_ms=250000\n",
                    "out_time_ms=750000\n",
                    "progress=end\n",
                ]
            )
            stderr = iter([])

            def wait(self):
                return 0

        with patch("services.media.subprocess.Popen", return_value=FakeProcess()):
            MediaProcessor.encode_subtitled_segment(
                video,
                subtitle,
                output,
                start_seconds=0.0,
                end_seconds=1.0,
                progress=progress,
                progress_task=7,
                progress_description="Remixing video_1.mp4",
            )

        self.assertIn(
            ("advance", 7, 0.25, "Remixing video_1.mp4"),
            progress.events,
        )
        self.assertIn(
            ("advance", 7, 0.5, "Remixing video_1.mp4"),
            progress.events,
        )
        self.assertEqual(
            progress.events[-1],
            (
                "advance",
                7,
                package_output_duration(1.0) - 0.75,
                "Remixing video_1.mp4",
            ),
        )

    def test_remix_segment_uses_shared_package_audio_filter(self):
        root = Path(tempfile.mkdtemp(prefix="remix-audio-rate-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        video = root / "video.mp4"
        subtitle = root / "video.ass"
        output = root / "segment.mp4"
        video.write_text("video", encoding="utf-8")
        subtitle.write_text("subtitle", encoding="utf-8")

        class FakeProcess:
            stdout = iter(["progress=end\n"])
            stderr = iter([])

            def wait(self):
                return 0

        with patch("services.media.subprocess.Popen", return_value=FakeProcess()) as popen:
            MediaProcessor.encode_subtitled_segment(
                video,
                subtitle,
                output,
                start_seconds=0.0,
                end_seconds=1.0,
            )

        cmd = popen.call_args.args[0]
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn(MediaProcessor._PACKAGE_VIDEO_FILTER, filter_complex)
        self.assertIn(MediaProcessor._PACKAGE_AUDIO_FILTER, filter_complex)
        self.assertIn("anoisesrc=", filter_complex)
        self.assertIn("a=0.008", filter_complex)
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "h264_nvenc")

    def test_encode_noise_segment_cuts_the_source_with_format_only_filters(self):
        root = Path(tempfile.mkdtemp(prefix="noise-progress-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        noise = root / "000.webm"
        noise.write_text("noise", encoding="utf-8")
        progress = FakeProgressReporter()

        class FakeProcess:
            stdout = iter(
                [
                    "out_time_ms=30000000\n",
                    "progress=end\n",
                ]
            )
            stderr = iter([])

            def wait(self):
                return 0

        with patch(
            "services.media.subprocess.Popen", return_value=FakeProcess()
        ) as popen:
            MediaProcessor.encode_noise_segment(
                cut=NoiseCut(
                    source=noise,
                    start_seconds=20800.0,
                    duration_seconds=60.0,
                ),
                output_file=root / "head.mp4",
                progress=progress,
                progress_task=7,
                progress_description="Noise for video_1.mp4",
            )

        cmd = popen.call_args.args[0]
        self.assertEqual(cmd[cmd.index("-ss") + 1], "20800.000")
        self.assertEqual(cmd[cmd.index("-t") + 1], "60.000")
        self.assertEqual(cmd[cmd.index("-i") + 1], str(noise))
        filter_complex = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn(MediaProcessor._NOISE_VIDEO_FILTER, filter_complex)
        self.assertIn(MediaProcessor._NOISE_AUDIO_FILTER, filter_complex)
        self.assertNotIn(MediaProcessor._PACKAGE_VIDEO_FILTER, filter_complex)
        self.assertNotIn(MediaProcessor._PACKAGE_AUDIO_FILTER, filter_complex)
        self.assertNotIn("anoisesrc=", filter_complex)
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "h264_nvenc")
        self.assertEqual(
            progress.events,
            [
                ("advance", 7, 30.0, "Noise for video_1.mp4"),
                ("advance", 7, 30.0, "Noise for video_1.mp4"),
            ],
        )

    def test_encode_noise_segment_raises_on_ffmpeg_failure(self):
        root = Path(tempfile.mkdtemp(prefix="noise-progress-fail-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        noise = root / "000.webm"
        noise.write_text("noise", encoding="utf-8")

        class FakeProcess:
            stdout = iter([])
            stderr = iter(["bad input\n"])

            def wait(self):
                return 1

        with patch(
            "services.media.subprocess.Popen", return_value=FakeProcess()
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                MediaProcessor.encode_noise_segment(
                    cut=NoiseCut(
                        source=noise,
                        start_seconds=0.0,
                        duration_seconds=60.0,
                    ),
                    output_file=root / "head.mp4",
                )

    def test_concat_remix_segments_captures_ffmpeg_output_and_suspends_progress(self):
        root = Path(tempfile.mkdtemp(prefix="concat-progress-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        inputs = []
        for index in range(3):
            input_file = root / f"{index}.mp4"
            input_file.write_text("video", encoding="utf-8")
            inputs.append(input_file)
        output = root / "out.mp4"
        progress = FakeProgressReporter()

        with patch("services.media.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                args=["ffmpeg"],
                returncode=0,
                stdout="",
                stderr="ffmpeg banner",
            )
            MediaProcessor.concat_remix_segments(
                inputs,
                output,
                progress=progress,
            )

        self.assertEqual(progress.suspended, 1)
        self.assertEqual(progress.events, [("suspend_enter",), ("suspend_exit",)])
        self.assertEqual(run.call_args.kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.PIPE)
        self.assertNotIn("check", run.call_args.kwargs)

    def test_build_remix_output_suspends_progress_during_concat(self):
        root = Path(tempfile.mkdtemp(prefix="build-remix-progress-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        video = root / "video.mp4"
        subtitle = root / "video.ass"
        noise = root / "000.webm"
        output = root / "out.mp4"
        for path in (video, subtitle, noise):
            path.write_text("media", encoding="utf-8")
        head = NoiseCut(
            source=noise, start_seconds=0.0, duration_seconds=60.0
        )
        tail = NoiseCut(
            source=noise, start_seconds=60.0, duration_seconds=60.0
        )
        progress = FakeProgressReporter()

        def fake_encode(**kwargs):
            kwargs["output_file"].write_text("segment", encoding="utf-8")

        with (
            patch.object(
                MediaProcessor,
                "encode_subtitled_segment",
                side_effect=fake_encode,
            ),
            patch.object(
                MediaProcessor,
                "encode_noise_segment",
                side_effect=fake_encode,
            ) as encode_noise_segment,
            patch.object(MediaProcessor, "concat_remix_segments") as concat,
        ):
            MediaProcessor.build_remix_output(
                video_file=video,
                subtitle_file=subtitle,
                output_file=output,
                head_noise=head,
                tail_noise=tail,
                start_seconds=0.0,
                end_seconds=1.0,
                progress=progress,
                progress_task=1,
            )

        self.assertIs(concat.call_args.kwargs["progress"], progress)
        concat_inputs = concat.call_args.args[0]
        self.assertEqual(
            [path.name for path in concat_inputs],
            ["head.mp4", "target.mp4", "tail.mp4"],
        )
        self.assertEqual(
            [call.kwargs["cut"] for call in encode_noise_segment.call_args_list],
            [head, tail],
        )


class RichProgressReporterTests(unittest.TestCase):
    def test_completed_chunk_task_does_not_render_during_next_stage(self):
        with open("NUL", "w", encoding="utf-8") as sink:
            reporter = RichProgressReporter(
                Console(force_terminal=False, file=sink)
            )
            with reporter:
                reporter.chunk_started(0, 1, 1, 10)
                reporter.chunk_finished(0, retries=0, cost=0.1)
                self.assertEqual(list(reporter.progress.tasks), [])

                task_id = reporter.start_stage(
                    "Burning subtitles", total=1.0
                )
                self.assertEqual(len(list(reporter.progress.tasks)), 1)
                reporter.finish(task_id)
                self.assertEqual(list(reporter.progress.tasks), [])


if __name__ == "__main__":
    unittest.main()
