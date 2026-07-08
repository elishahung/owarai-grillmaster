"""Public workflow orchestration entry points."""

from contextlib import nullcontext
from dataclasses import dataclass

from loguru import logger

from project import Project, ProgressStage
from settings import settings
from services.progress import NoopProgressReporter, create_progress_reporter

from .delivery import deliver_project
from .runner import StageSpec, WorkflowRunner
from .side_tasks import SideTaskManager
from .stages import media, metadata, postprocess, transcription, translation


@dataclass(frozen=True)
class WorkflowOptions:
    """Runtime options that affect one project processing invocation."""

    break_after: ProgressStage | None = None
    enable_refine: bool = False
    enable_glossary_check: bool = False
    enable_cover: bool = False
    enable_date_research: bool = False
    remix_noise_name: str | None = None
    remix_prefix: bool = False
    section_start: float | None = None
    section_end: float | None = None

    @property
    def do_refine(self) -> bool:
        return self.enable_refine or settings.enable_postprocess_refine

    @property
    def do_glossary_check(self) -> bool:
        return (
            self.enable_glossary_check
            or settings.enable_postprocess_glossary_check
        )

    @property
    def do_cover(self) -> bool:
        return self.enable_cover or settings.enable_cover_generation

    @property
    def do_date_research(self) -> bool:
        return (
            self.enable_date_research
            or settings.enable_broadcast_date_agent_fallback
        )

    @property
    def has_section(self) -> bool:
        return self.section_start is not None or self.section_end is not None

    @property
    def allow_side_tasks(self) -> bool:
        return self.break_after is None


def submit_project(
    source_str: str,
    translation_hint: str | None = None,
    break_after: ProgressStage | None = None,
    parent_project_path: str | None = None,
    enable_refine: bool = False,
    enable_glossary_check: bool = False,
    enable_cover: bool = False,
    enable_date_research: bool = False,
    remix_noise_name: str | None = None,
    remix_prefix: bool = False,
    section_start: float | None = None,
    section_end: float | None = None,
) -> None:
    """Submit a new video project for processing."""
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
        enable_glossary_check=enable_glossary_check,
        enable_cover=enable_cover,
        enable_date_research=enable_date_research,
        remix_noise_name=remix_noise_name,
        remix_prefix=remix_prefix,
        section_start=section_start,
        section_end=section_end,
    )


def process_project(
    project_id: str,
    break_after: ProgressStage | None = None,
    enable_refine: bool = False,
    enable_glossary_check: bool = False,
    enable_cover: bool = False,
    enable_date_research: bool = False,
    remix_noise_name: str | None = None,
    remix_prefix: bool = False,
    section_start: float | None = None,
    section_end: float | None = None,
    progress: NoopProgressReporter | None = None,
) -> None:
    """Process a video project with an auto-enabled CLI progress reporter."""
    progress_context = (
        create_progress_reporter()
        if progress is None
        else nullcontext(progress)
    )
    with progress_context as active_progress:
        _process_project_impl(
            project_id,
            options=WorkflowOptions(
                break_after=break_after,
                enable_refine=enable_refine,
                enable_glossary_check=enable_glossary_check,
                enable_cover=enable_cover,
                enable_date_research=enable_date_research,
                remix_noise_name=remix_noise_name,
                remix_prefix=remix_prefix,
                section_start=section_start,
                section_end=section_end,
            ),
            progress=active_progress,
        )


def _process_project_impl(
    project_id: str,
    options: WorkflowOptions,
    progress: NoopProgressReporter | None = None,
) -> None:
    """Process a project through the resumable captioning pipeline."""
    logger.info(f"Starting project processing: {project_id}")
    if progress is None:
        progress = NoopProgressReporter()

    project: Project | None = None
    pipeline_error: Exception | None = None

    try:
        project = Project.from_source_str(project_id)
        runner = WorkflowRunner(
            project=project,
            project_id=project_id,
            break_after=options.break_after,
        )
        with SideTaskManager(project) as side_tasks:
            if runner.run(
                StageSpec(
                    stage=ProgressStage.METADATA_FETCHED,
                    start_message="Fetching metadata",
                    complete_message="Metadata fetched",
                    skipped_message="Metadata already fetched",
                ),
                lambda: metadata.fetch_metadata(project),
            ):
                return

            side_tasks.apply_cached_date_research_if_available()
            side_tasks.start_date_research_if_needed(
                enabled=options.do_date_research,
                allow_side_tasks=options.allow_side_tasks,
            )

            if runner.run(
                StageSpec(
                    stage=ProgressStage.DOWNLOADED,
                    start_message="Downloading video",
                    complete_message="Video downloaded",
                    skipped_message="Video already downloaded",
                ),
                lambda: media.download_project_video(project),
            ):
                return

            side_tasks.start_cover_if_needed(
                enabled=options.do_cover,
                allow_side_tasks=options.allow_side_tasks,
            )

            if runner.run(
                StageSpec(
                    stage=ProgressStage.VIDEO_PROCESSED,
                    start_message="Combining video segments",
                    complete_message="Video processed",
                    skipped_message="Video already processed",
                    on_skip=(
                        media.warn_section_ignored
                        if options.has_section
                        else None
                    ),
                ),
                lambda: media.process_video(
                    project,
                    section_start=options.section_start,
                    section_end=options.section_end,
                ),
            ):
                return

            if runner.run(
                StageSpec(
                    stage=ProgressStage.AUDIO_PROCESSED,
                    start_message="Extracting audio",
                    complete_message="Audio extracted",
                    skipped_message="Audio already extracted",
                ),
                lambda: media.extract_audio(project),
            ):
                return

            if runner.run(
                StageSpec(
                    stage=ProgressStage.ASR_COMPLETED,
                    start_message="Running ASR",
                    complete_message="ASR completed",
                    skipped_message="ASR already completed",
                ),
                lambda: transcription.run_asr(project),
            ):
                return

            if runner.run(
                StageSpec(
                    stage=ProgressStage.SRT_COMPLETED,
                    start_message="Converting ASR JSON to SRT",
                    complete_message="SRT generated",
                    skipped_message="SRT already generated",
                ),
                lambda: transcription.convert_asr_to_srt(project),
            ):
                return

            if runner.run(
                StageSpec(
                    stage=ProgressStage.PREPASS_COMPLETED,
                    start_message="Running pre-pass",
                    complete_message="Pre-pass completed",
                    skipped_message="Pre-pass already completed",
                ),
                lambda: translation.run_pre_pass(project),
            ):
                return

            if runner.run(
                StageSpec(
                    stage=ProgressStage.CHUNK_TRANSLATED,
                    start_message="Translating subtitles",
                    complete_message="Chunk translation completed",
                    skipped_message="Chunk translation already completed",
                ),
                lambda: translation.translate_chunks(project, progress),
            ):
                return

            if runner.run_optional(
                enabled=options.do_refine,
                disabled_message="SRT refinement disabled",
                spec=StageSpec(
                    stage=ProgressStage.SRT_REFINED,
                    start_message="Refining subtitles",
                    complete_message="Subtitles refined",
                    skipped_message="Subtitles already refined",
                ),
                action=lambda: postprocess.refine_project_subtitles(project),
            ):
                return

            if runner.run_optional(
                enabled=options.do_glossary_check,
                disabled_message="Glossary check disabled",
                spec=StageSpec(
                    stage=ProgressStage.GLOSSARY_CHECKED,
                    start_message="Glossary-checking subtitles",
                    complete_message="Subtitles glossary-checked",
                    skipped_message="Subtitles already glossary-checked",
                ),
                action=lambda: postprocess.glossary_check_project_subtitles(
                    project
                ),
            ):
                return

            if runner.run(
                StageSpec(
                    stage=ProgressStage.FINALIZED,
                    start_message="Finalizing subtitles",
                    complete_message="Finalized (ASS + SRT)",
                    skipped_message="Already finalized",
                ),
                lambda: postprocess.finalize_project_subtitles(project),
            ):
                return

    except Exception as e:
        pipeline_error = e
        logger.error(f"Project processing failed for {project_id}: {e}")

    if pipeline_error is not None:
        raise pipeline_error

    if project is None:
        raise RuntimeError(f"Project could not be loaded: {project_id}")

    deliver_project(
        project=project,
        project_id=project_id,
        progress=progress,
        remix_noise_name=options.remix_noise_name,
        remix_prefix=options.remix_prefix,
    )
