"""Download and media-processing workflow stages."""

from loguru import logger

from project import Project
from services.media import MediaProcessor
from services.ytdlp import download_video, normalize_official_subtitle
from settings import settings


def download_project_video(project: Project) -> None:
    download_video(project.source_url, project.project_path)


def process_video(
    project: Project,
    *,
    section_start: float | None,
    section_end: float | None,
) -> None:
    has_section = section_start is not None or section_end is not None
    if has_section:
        # Keep the uncut combine as video.full.mp4, then cut the requested
        # section locally. Skip the combine on resume if the full video exists.
        if not project.full_video_path.exists():
            MediaProcessor.combine_videos(
                project.downloaded_video_paths,
                project.full_video_path,
            )
        MediaProcessor.cut_video(
            project.full_video_path,
            project.video_path,
            start_seconds=section_start,
            end_seconds=section_end,
        )
    else:
        MediaProcessor.combine_videos(
            project.downloaded_video_paths,
            project.video_path,
        )

    if settings.enable_official_subtitles:
        try:
            normalize_official_subtitle(
                project.downloaded_subtitle_paths,
                project.official_subtitle_path,
                section_start=section_start,
                section_end=section_end,
            )
        except Exception as subtitle_error:
            logger.warning(
                "Official subtitle normalization failed: "
                f"{subtitle_error}"
            )


def warn_section_ignored() -> None:
    logger.warning(
        "Video already processed; --start/--to are ignored on resume"
    )


def extract_audio(project: Project) -> None:
    MediaProcessor.extract_audio(project.video_path, project.audio_path)
