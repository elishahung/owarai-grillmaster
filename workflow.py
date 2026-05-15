"""Main workflow orchestration for video captioning pipeline.

This module provides the main processing function that coordinates all stages
of the video captioning workflow, from fetching metadata to translation.
"""

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from project import Project, ProgressStage, VideoSource
from loguru import logger
from settings import settings
from services.finalize import convert_file as convert_srt_to_ass
from services.codex import generate_cover, refine_subtitles
from services.elevenlabs import ElevenLabsASR, convert_file
from services.gemini import Gemini, GeminiTranslationError, TranslationRequest
from services.media import MediaProcessor
from services.package import package_project
from services.ytdlp import (
    download_video,
    get_abema_episode_talents,
    get_tver_episode_talents,
    get_video_info,
)


def submit_project(
    source_str: str,
    translation_hint: str | None = None,
    break_after: ProgressStage | None = None,
    parent_project_path: str | None = None,
    enable_refine: bool = False,
    enable_cover: bool = False,
) -> None:
    """Submit a new video project for processing.

    This function creates a new project with the given video source and
    optional description, saves it to disk, and immediately starts processing
    through the captioning pipeline.

    Args:
        source_str: The video source, id or url (e.g., 'BV1ZArvBaEqL', 'https://www.bilibili.com/video/BV1ZArvBaEqL').
        translation_hint: Optional description of the video content. If not provided,
            the video's title will be used as description during metadata fetching.
        break_after: Optional progress stage to stop after.
        parent_project_path: Optional filesystem path to a parent project
            directory whose pre_pass.json should seed this project's pre-pass
            for cross-episode consistency.
        enable_refine: Force-enable the optional subtitle refinement stage.
            Overrides ``settings.enable_srt_refine`` when True.
        enable_cover: Force-enable the optional async cover image stylization.
            Overrides ``settings.enable_cover_generation`` when True. Always
            skipped when ``break_after`` is set.

    Note:
        The project will be automatically saved to the projects directory before
        processing begins.
    """
    logger.info(f"Submitting new project: {source_str}")
    new_project = Project.from_source_str(
        source_str=source_str,
        translation_hint=translation_hint,
        parent_project_path=parent_project_path,
    )
    new_project.save()
    logger.info(f"Project saved: {source_str}")
    process_project(
        new_project.id,
        break_after=break_after,
        enable_refine=enable_refine,
        enable_cover=enable_cover,
    )


def _should_stop_after_stage(
    project_id: str,
    break_after: ProgressStage | None,
    completed_stage: ProgressStage,
) -> bool:
    """Return whether workflow should stop after reaching a stage."""
    if break_after != completed_stage:
        return False

    logger.warning(
        f"Breakpoint reached after {completed_stage.value}; "
        f"stopping project processing: {project_id}"
    )
    return True


def _make_translation_request(
    project: Project, project_id: str
) -> TranslationRequest:
    """Build the Gemini request shared by the pre-pass and chunk stages.

    Both stages must pass identical inputs so chunk boundaries and the
    persisted pre_pass.json stay consistent across the split.
    """
    return TranslationRequest(
        video_description=project.translation_hint,
        srt_path=project.srt_path,
        audio_key=project_id,
        video_path=project.video_path,
        audio_path=project.audio_path,
        output_path=project.translated_path,
        pre_pass_path=project.pre_pass_path,
        pre_pass_cache_dir=project.pre_pass_cache_dir,
        chunks_cache_dir=project.chunks_cache_dir,
        source_metadata_context=project.source_metadata_context(),
        parent_pre_pass_context=project.parent_pre_pass_context(),
    )


def process_project(
    project_id: str,
    break_after: ProgressStage | None = None,
    enable_refine: bool = False,
    enable_cover: bool = False,
) -> None:
    """Process a video project through the complete captioning pipeline.

    This function orchestrates the entire workflow:
    1. Fetch video metadata from source
    2. Download video (kicks off async cover generation if enabled)
    3. Combine downloaded video segments
    4. Extract audio from video
    5. Perform automatic speech recognition (ASR) and write source SRT
    6. Translate subtitles using Gemini
    7. Refine Traditional Chinese subtitles via Codex (optional)
    8. Finalize: emit styled ASS + cleaned SRT from refined/translated SRT
    9. Wait for cover image generation, then archive (optional)

    Each stage is skipped if it has already been completed (idempotent).
    Progress is automatically saved after each stage.

    Args:
        project_id: The unique identifier for the project.
        break_after: Optional progress stage to stop after. If the stage is
            already complete on a resumed project, processing stops before the
            next stage.
        enable_refine: Force-enable the optional subtitle refinement stage.
            Overrides ``settings.enable_srt_refine`` when True.
        enable_cover: Force-enable the optional async cover image stylization.
            Overrides ``settings.enable_cover_generation`` when True. Always
            skipped when ``break_after`` is set.

    Raises:
        Exception: If any required stage of the processing fails.
    """
    logger.info(f"Starting project processing: {project_id}")
    do_refine = enable_refine or settings.enable_srt_refine
    do_cover = enable_cover or settings.enable_cover_generation
    cover_executor: ThreadPoolExecutor | None = None
    cover_future: Future | None = None
    project: Project | None = None
    pipeline_error: Exception | None = None

    try:
        project = Project.from_source_str(project_id)
        translation_result = None

        # Fetch metadata
        if not project.is_metadata_fetched:
            logger.info(f"Stage: Fetching metadata for {project_id}")
            video_data = get_video_info(project.source_url)
            project.update_from_video_info(video_data)
            if project.source == VideoSource.TVER:
                talents = get_tver_episode_talents(project.id)
                if talents:
                    project.update_from_source_talents(talents)
            if project.source == VideoSource.ABEMA:
                talents = get_abema_episode_talents(project.id)
                if talents:
                    project.update_from_source_talents(talents)
            project.mark_progress(ProgressStage.METADATA_FETCHED)
            logger.success("Stage complete: Metadata fetched")
        else:
            logger.debug("Stage skipped: Metadata already fetched")
        if _should_stop_after_stage(
            project_id, break_after, ProgressStage.METADATA_FETCHED
        ):
            return

        # Download video
        if not project.is_downloaded:
            logger.info(f"Stage: Downloading video for {project_id}")
            download_video(project.source_url, project.project_path)
            project.mark_progress(ProgressStage.DOWNLOADED)
            logger.success("Stage complete: Video downloaded")
        else:
            logger.debug("Stage skipped: Video already downloaded")
        if _should_stop_after_stage(
            project_id, break_after, ProgressStage.DOWNLOADED
        ):
            return

        # Start async cover generation (parallel to remaining stages)
        if do_cover and break_after is None and not project.is_cover_generated:
            logger.info(
                f"Stage: Starting async cover generation for {project_id}"
            )
            cover_executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="cover"
            )
            cover_future = cover_executor.submit(generate_cover, project)

        # Process video
        if not project.is_video_processed:
            logger.info(f"Stage: Combining video segments for {project_id}")
            MediaProcessor.combine_videos(
                project.downloaded_video_paths,
                project.video_path,
            )
            project.mark_progress(ProgressStage.VIDEO_PROCESSED)
            logger.success("Stage complete: Video processed")
        else:
            logger.debug("Stage skipped: Video already processed")
        if _should_stop_after_stage(
            project_id, break_after, ProgressStage.VIDEO_PROCESSED
        ):
            return

        # Process audio
        if not project.is_audio_processed:
            logger.info(f"Stage: Extracting audio for {project_id}")
            MediaProcessor.extract_audio(project.video_path, project.audio_path)
            project.mark_progress(ProgressStage.AUDIO_PROCESSED)
            logger.success("Stage complete: Audio extracted")
        else:
            logger.debug("Stage skipped: Audio already extracted")
        if _should_stop_after_stage(
            project_id, break_after, ProgressStage.AUDIO_PROCESSED
        ):
            return

        # Process ASR
        if not project.is_asr_completed:
            logger.info(f"Stage: Running ASR for {project_id}")
            asr = ElevenLabsASR()
            transcription_result = asr.transcribe_to_file(
                project.audio_path, project.asr_path
            )
            if transcription_result.total_cost > 0:
                project.add_cost("elevenlabs", transcription_result.total_cost)
            logger.info(
                f"Stage ASR cost: ${transcription_result.total_cost:.4f} "
                f"for {transcription_result.audio_duration_secs:.2f}s"
            )
            project.mark_progress(ProgressStage.ASR_COMPLETED)
            logger.success("Stage complete: ASR completed")
        else:
            logger.debug("Stage skipped: ASR already completed")
        if _should_stop_after_stage(
            project_id, break_after, ProgressStage.ASR_COMPLETED
        ):
            return

        # Process SRT
        if not project.is_srt_completed:
            logger.info(f"Stage: Converting ASR JSON to SRT for {project_id}")
            convert_file(project.asr_path, project.srt_path)
            project.mark_progress(ProgressStage.SRT_COMPLETED)
            logger.success("Stage complete: SRT generated")
        else:
            logger.debug("Stage skipped: SRT already generated")
        if _should_stop_after_stage(
            project_id, break_after, ProgressStage.SRT_COMPLETED
        ):
            return

        # Process pre-pass
        if not project.is_prepass_completed:
            logger.info(f"Stage: Running pre-pass for {project_id}")
            gemini = Gemini()
            try:
                prepass_result = gemini.run_pre_pass(
                    _make_translation_request(project, project_id)
                )
            except GeminiTranslationError as e:
                if e.summary.total_cost > 0:
                    project.add_cost("gemini", e.summary.total_cost)
                logger.error(
                    f"Stage failed: Pre-pass partial cost "
                    f"${e.summary.total_cost:.4f}"
                )
                raise
            if prepass_result.total_cost > 0:
                project.add_cost("gemini", prepass_result.total_cost)
            project.mark_progress(ProgressStage.PREPASS_COMPLETED)
            logger.success("Stage complete: Pre-pass completed")
        else:
            logger.debug("Stage skipped: Pre-pass already completed")
        if _should_stop_after_stage(
            project_id, break_after, ProgressStage.PREPASS_COMPLETED
        ):
            return

        # Process chunk translation
        if not project.is_chunk_translated:
            logger.info(f"Stage: Translating subtitles for {project_id}")
            gemini = Gemini()
            try:
                translation_result = gemini.translate_chunks(
                    _make_translation_request(project, project_id)
                )
            except GeminiTranslationError as e:
                if e.summary.total_cost > 0:
                    project.add_cost("gemini", e.summary.total_cost)
                logger.error(
                    f"Stage failed: Translation partial cost "
                    f"${e.summary.total_cost:.4f} "
                    f"(completed {e.summary.completed_chunks}/{e.summary.num_chunks}, "
                    f"retries={e.summary.retries})"
                )
                raise
            if translation_result.total_cost > 0:
                project.add_cost("gemini", translation_result.total_cost)
            project.mark_progress(ProgressStage.CHUNK_TRANSLATED)
            logger.success("Stage complete: Chunk translation completed")
        else:
            logger.debug("Stage skipped: Chunk translation already completed")
        if _should_stop_after_stage(
            project_id, break_after, ProgressStage.CHUNK_TRANSLATED
        ):
            return

        # Process subtitle refinement (optional)
        if do_refine:
            if not project.is_srt_refined:
                logger.info(f"Stage: Refining subtitles for {project_id}")
                refine_subtitles(project)
                project.mark_progress(ProgressStage.SRT_REFINED)
                logger.success("Stage complete: Subtitles refined")
            else:
                logger.debug("Stage skipped: Subtitles already refined")
            if _should_stop_after_stage(
                project_id, break_after, ProgressStage.SRT_REFINED
            ):
                return
        else:
            logger.debug("Stage skipped: SRT refinement disabled")

        # Finalize: produce ASS + SRT outputs together
        if not project.is_finalized:
            logger.info(f"Stage: Finalizing subtitles for {project_id}")
            srt_source = (
                project.refined_srt_path
                if project.refined_srt_path.exists()
                else project.translated_path
            )
            convert_srt_to_ass(
                srt_source,
                project.ass_path,
                finalized_srt_path=project.finalized_srt_path,
            )
            project.mark_progress(ProgressStage.FINALIZED)
            logger.success("Stage complete: Finalized (ASS + SRT)")
        else:
            logger.debug("Stage skipped: Already finalized")
        if _should_stop_after_stage(
            project_id, break_after, ProgressStage.FINALIZED
        ):
            return

    except Exception as e:
        pipeline_error = e
        logger.error(f"Project processing failed for {project_id}: {e}")
    finally:
        # Always wait for cover generation to finish, even on pipeline error.
        # codex subscription cost is already incurred; abandoning mid-flight
        # would orphan the subprocess and lose the work.
        if cover_future is not None and project is not None:
            try:
                cover_future.result(
                    timeout=settings.codex_default_timeout_secs * 2
                )
                project.is_cover_generated = True
                project.save()
                logger.success("Stage complete: Cover generated")
            except Exception as cover_error:
                logger.warning(f"Cover generation failed: {cover_error}")
        if cover_executor is not None:
            cover_executor.shutdown(wait=False)

    if pipeline_error is not None:
        raise pipeline_error

    logger.success(f"Project processing complete: {project_id}")

    # Archive project
    archived_location: Path | None = None
    if settings.archived_path is not None:
        archived_location = project.archive()
    else:
        logger.warning("Archived path is not set, skipping archiving")

    # Package project (burn-in + cover copy)
    if settings.package_path is not None:
        source_root = archived_location or project.project_path
        package_project(project, source_root, settings.package_path)

    logger.info(
        f"Project {project_id} total accumulated API cost: "
        f"${project.total_cost:.4f}"
    )
