"""Public workflow orchestration entry points."""

from contextlib import nullcontext
from dataclasses import dataclass

from loguru import logger

from project import Project, ProgressStage
from settings import settings
from services.progress import (
    NoopProgressReporter,
    PlannedStage,
    create_progress_reporter,
)

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
    progress: NoopProgressReporter | None = None,
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
        progress=progress,
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
        try:
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
        except Exception as e:
            active_progress.pipeline_failed(str(e))
            raise
        else:
            active_progress.pipeline_completed()


def _stage_specs(options: WorkflowOptions) -> dict[str, StageSpec]:
    """Build the ordered stage specs with their display-parameter snapshots."""
    # Mirrors the api-vs-agent concurrency split in services/translate/facade.
    chunk_concurrency = (
        settings.chunk_api_concurrency
        if settings.agent_chunk_model.backend == "gemini-api"
        else settings.chunk_agent_concurrency
    )
    combine_params: dict[str, str] = {"tool": "ffmpeg"}
    if options.has_section:
        combine_params["section"] = (
            f"{options.section_start or 0:g}s–"
            f"{'end' if options.section_end is None else f'{options.section_end:g}s'}"
        )
    return {
        "metadata": StageSpec(
            stage=ProgressStage.METADATA_FETCHED,
            key="metadata",
            start_message="Fetching metadata",
            complete_message="Metadata fetched",
            skipped_message="Metadata already fetched",
            params={
                "official_cc": (
                    "on" if settings.enable_official_subtitles else "off"
                ),
            },
        ),
        "download": StageSpec(
            stage=ProgressStage.DOWNLOADED,
            key="download",
            start_message="Downloading video",
            complete_message="Video downloaded",
            skipped_message="Video already downloaded",
            params={"tool": "yt-dlp"},
        ),
        "combine": StageSpec(
            stage=ProgressStage.VIDEO_PROCESSED,
            key="combine",
            start_message="Combining video segments",
            complete_message="Video processed",
            skipped_message="Video already processed",
            on_skip=(
                media.warn_section_ignored if options.has_section else None
            ),
            params=combine_params,
        ),
        "audio": StageSpec(
            stage=ProgressStage.AUDIO_PROCESSED,
            key="audio",
            start_message="Extracting audio",
            complete_message="Audio extracted",
            skipped_message="Audio already extracted",
            params={"tool": "ffmpeg"},
        ),
        "asr": StageSpec(
            stage=ProgressStage.ASR_COMPLETED,
            key="asr",
            start_message="Running ASR",
            complete_message="ASR completed",
            skipped_message="ASR already completed",
            params={
                "model": settings.elevenlabs_stt_model,
                "language": settings.elevenlabs_stt_language_code,
            },
        ),
        "srt": StageSpec(
            stage=ProgressStage.SRT_COMPLETED,
            key="srt",
            start_message="Converting ASR JSON to SRT",
            complete_message="SRT generated",
            skipped_message="SRT already generated",
        ),
        "prepass": StageSpec(
            stage=ProgressStage.PREPASS_COMPLETED,
            key="prepass",
            start_message="Running pre-pass",
            complete_message="Pre-pass completed",
            skipped_message="Pre-pass already completed",
            params={
                "model": str(settings.agent_prepass_model),
                "frame_interval": (
                    f"{settings.prepass_frame_interval_seconds}s"
                ),
            },
        ),
        "chunks": StageSpec(
            stage=ProgressStage.CHUNK_TRANSLATED,
            key="chunks",
            start_message="Translating subtitles",
            complete_message="Chunk translation completed",
            skipped_message="Chunk translation already completed",
            params={
                "model": str(settings.agent_chunk_model),
                "concurrency": str(chunk_concurrency),
                "char_limit": str(settings.chunk_char_limit),
                "max_retries": str(settings.chunk_max_retries),
            },
        ),
        "refine": StageSpec(
            stage=ProgressStage.SRT_REFINED,
            key="refine",
            start_message="Refining subtitles",
            complete_message="Subtitles refined",
            skipped_message="Subtitles already refined",
            params={"model": str(settings.agent_postprocess_model)},
        ),
        "glossary": StageSpec(
            stage=ProgressStage.GLOSSARY_CHECKED,
            key="glossary",
            start_message="Glossary-checking subtitles",
            complete_message="Subtitles glossary-checked",
            skipped_message="Subtitles already glossary-checked",
            params={"model": str(settings.agent_postprocess_model)},
        ),
        "finalize": StageSpec(
            stage=ProgressStage.FINALIZED,
            key="finalize",
            start_message="Finalizing subtitles",
            complete_message="Finalized (ASS + SRT)",
            skipped_message="Already finalized",
        ),
    }


def _build_plan(
    options: WorkflowOptions, specs: dict[str, StageSpec]
) -> list[PlannedStage]:
    """Describe everything this run may execute, for the progress reporter."""
    optional_enabled = {
        "refine": options.do_refine,
        "glossary": options.do_glossary_check,
    }
    plan = [
        PlannedStage(
            key=spec.key,
            label=spec.start_message,
            params=spec.params,
            enabled=optional_enabled.get(spec.key, True),
        )
        for spec in specs.values()
    ]
    # Delivery steps only happen without a breakpoint and when configured.
    if options.break_after is None:
        if settings.archived_path is not None:
            plan.append(PlannedStage(key="archive", label="Archiving project"))
        if settings.package_path is not None:
            plan.append(
                PlannedStage(key="package", label="Packaging deliverable")
            )
    plan.append(
        PlannedStage(
            key="date",
            label="Broadcast-date research",
            params={"model": str(settings.agent_common_model), "web_search": "on"},
            kind="side_task",
            enabled=options.do_date_research and options.allow_side_tasks,
        )
    )
    plan.append(
        PlannedStage(
            key="cover",
            label="Cover generation",
            params={
                "backend": "codex",
                "effort": settings.agent_common_model.reasoning_effort,
            },
            kind="side_task",
            enabled=options.do_cover and options.allow_side_tasks,
        )
    )
    return plan


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
    specs = _stage_specs(options)

    try:
        project = Project.from_source_str(project_id)
        progress.pipeline_started(project, _build_plan(options, specs))
        runner = WorkflowRunner(
            project=project,
            project_id=project_id,
            break_after=options.break_after,
            progress=progress,
        )
        with SideTaskManager(project, progress=progress) as side_tasks:
            if runner.run(
                specs["metadata"],
                lambda: metadata.fetch_metadata(project),
            ):
                return

            side_tasks.apply_cached_date_research_if_available()
            side_tasks.start_date_research_if_needed(
                enabled=options.do_date_research,
                allow_side_tasks=options.allow_side_tasks,
            )

            if runner.run(
                specs["download"],
                lambda: media.download_project_video(project, progress),
            ):
                return

            side_tasks.start_cover_if_needed(
                enabled=options.do_cover,
                allow_side_tasks=options.allow_side_tasks,
            )

            if runner.run(
                specs["combine"],
                lambda: media.process_video(
                    project,
                    section_start=options.section_start,
                    section_end=options.section_end,
                ),
            ):
                return

            if runner.run(
                specs["audio"],
                lambda: media.extract_audio(project),
            ):
                return

            if runner.run(
                specs["asr"],
                lambda: transcription.run_asr(project),
            ):
                return

            if runner.run(
                specs["srt"],
                lambda: transcription.convert_asr_to_srt(project),
            ):
                return

            if runner.run(
                specs["prepass"],
                lambda: translation.run_pre_pass(project),
            ):
                return

            if runner.run(
                specs["chunks"],
                lambda: translation.translate_chunks(project, progress),
            ):
                return

            if runner.run_optional(
                enabled=options.do_refine,
                disabled_message="SRT refinement disabled",
                spec=specs["refine"],
                action=lambda: postprocess.refine_project_subtitles(project),
            ):
                return

            if runner.run_optional(
                enabled=options.do_glossary_check,
                disabled_message="Glossary check disabled",
                spec=specs["glossary"],
                action=lambda: postprocess.glossary_check_project_subtitles(
                    project
                ),
            ):
                return

            if runner.run(
                specs["finalize"],
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
