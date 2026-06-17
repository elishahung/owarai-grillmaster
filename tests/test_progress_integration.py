import asyncio
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import workflow as workflow_module
from services.gemini.chunk_worker import ChunkTranslationResult
from services.gemini.errors import (
    ChunkTranslationError,
    GeminiTranslationError,
    TranslationCostSummary,
)
from services.gemini.gemini import Gemini, TranslationRequest
from services.gemini.pre_pass import PrePassResult
from services.media import MediaProcessor
from services.progress import RichProgressReporter
from services.srt import SrtBlock, parse_srt
from rich.console import Console


class FakeProgressReporter:
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


class WorkflowProgressTests(unittest.TestCase):
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
        project.is_srt_refined = False
        project.is_glossary_checked = False
        project.is_cover_generated = False
        project.is_finalized = True
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
                workflow_module.Project, "from_source_str", return_value=project
            ),
            patch.object(workflow_module, "Gemini") as gemini_cls,
            patch.object(workflow_module.settings, "archived_path", None),
            patch.object(workflow_module.settings, "package_path", None),
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
        self.assertEqual(progress.events, [])


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
            audio_key="demo",
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
        gemini = Gemini.__new__(Gemini)
        gemini.client = object()

        async def fake_translate(
            client, media_assets, chunk, chunk_index, total_chunks, pre_pass
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
                "services.gemini.gemini.split_into_chunks",
                return_value=[[blocks[0]], [blocks[1]]],
            ),
            patch(
                "services.gemini.gemini.prepare_chunk_media_assets",
                return_value=MagicMock(),
            ),
            patch(
                "services.gemini.gemini.translate_chunk",
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
        gemini = Gemini.__new__(Gemini)
        gemini.client = object()

        async def fake_translate(
            client, media_assets, chunk, chunk_index, total_chunks, pre_pass
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
                "services.gemini.gemini.split_into_chunks",
                return_value=[[blocks[0]], [blocks[1]]],
            ),
            patch(
                "services.gemini.gemini.prepare_chunk_media_assets",
                return_value=MagicMock(),
            ),
            patch(
                "services.gemini.gemini.translate_chunk",
                side_effect=fake_translate,
            ),
        ):
            with self.assertRaises(GeminiTranslationError):
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

        with (
            patch.object(MediaProcessor, "get_media_duration", return_value=1.0),
            patch("services.media.subprocess.Popen", return_value=FakeProcess()),
        ):
            MediaProcessor.burn_in_subtitles(
                video, subtitle, output, progress=progress
            )

        self.assertIn(("start_stage", 1, "Burning subtitles", 1.0), progress.events)
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
            patch.object(MediaProcessor, "get_media_duration", return_value=1.0),
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
            ("advance", 7, 0.25, "Remixing video_1.mp4"),
        )

    def test_remix_segment_resamples_audio_to_noise_chunk_rate(self):
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
        self.assertIn("aresample=48000:async=1", filter_complex)

    def test_prepare_noise_reports_existing_and_encoded_chunk_progress(self):
        root = Path(tempfile.mkdtemp(prefix="noise-progress-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        noise = root / "sleep.webm"
        output_dir = root / "sleep"
        output_dir.mkdir()
        noise.write_text("noise", encoding="utf-8")
        existing = output_dir / "000.mp4"
        existing.write_text("prepared", encoding="utf-8")
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

        with (
            patch.object(MediaProcessor, "get_media_duration", return_value=2.0),
            patch("services.media.subprocess.Popen", return_value=FakeProcess()) as popen,
        ):
            MediaProcessor.prepare_noise_chunks(
                noise_file=noise,
                output_dir=output_dir,
                chunk_duration_seconds=1,
                progress=progress,
            )

        self.assertEqual(
            progress.events[0],
            ("start_stage", 1, "Preparing noise", 2),
        )
        self.assertIn(
            ("advance", 1, 1, "Preparing noise 1/2"),
            progress.events,
        )
        self.assertIn(
            ("advance", 1, 0.5, "Preparing noise 2/2"),
            progress.events,
        )
        cmd = popen.call_args.args[0]
        self.assertEqual(cmd[cmd.index("-af") + 1], "aresample=48000:async=1")
        self.assertEqual(progress.events[-1], ("finish", 1, "done"))

    def test_prepare_noise_marks_progress_failed_on_ffmpeg_failure(self):
        root = Path(tempfile.mkdtemp(prefix="noise-progress-fail-test-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        noise = root / "sleep.webm"
        output_dir = root / "sleep"
        noise.write_text("noise", encoding="utf-8")
        progress = FakeProgressReporter()

        class FakeProcess:
            stdout = iter([])
            stderr = iter(["bad input\n"])

            def wait(self):
                return 1

        with (
            patch.object(MediaProcessor, "get_media_duration", return_value=1.0),
            patch("services.media.subprocess.Popen", return_value=FakeProcess()),
        ):
            with self.assertRaises(Exception):
                MediaProcessor.prepare_noise_chunks(
                    noise_file=noise,
                    output_dir=output_dir,
                    chunk_duration_seconds=1,
                    progress=progress,
                )

        self.assertEqual(progress.events[-1], ("finish", 1, "failed"))

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
        noise = root / "noise.mp4"
        output = root / "out.mp4"
        for path in (video, subtitle, noise):
            path.write_text("media", encoding="utf-8")
        progress = FakeProgressReporter()

        def fake_encode(**kwargs):
            kwargs["output_file"].write_text("segment", encoding="utf-8")

        with (
            patch.object(
                MediaProcessor,
                "encode_subtitled_segment",
                side_effect=fake_encode,
            ),
            patch.object(MediaProcessor, "concat_remix_segments") as concat,
        ):
            MediaProcessor.build_remix_output(
                video_file=video,
                subtitle_file=subtitle,
                output_file=output,
                noise_file=noise,
                start_seconds=0.0,
                end_seconds=1.0,
                progress=progress,
                progress_task=1,
            )

        self.assertIs(concat.call_args.kwargs["progress"], progress)
        concat_inputs = concat.call_args.args[0]
        self.assertEqual(len(concat_inputs), 2)
        self.assertEqual(concat_inputs[0], noise)
        self.assertEqual(concat_inputs[1].name, "target.mp4")


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
