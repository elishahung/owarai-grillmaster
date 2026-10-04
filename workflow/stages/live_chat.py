"""Optional live-chat replay stages (`--chat`)."""

from project import Project
from services.live_chat import (
    ChatTranslationInputs,
    parse_live_chat,
    translate_live_chat,
)
from services.progress import NoopProgressReporter
from services.ytdlp import download_live_chat


def fetch_project_live_chat(project: Project) -> None:
    """Download the replay and normalize it onto the `video.mp4` timeline.

    Runs right after the video is processed so a video without a replay
    fails before ASR spends money, and so the section the video was cut to
    is already recorded on the project.
    """
    if (
        project.full_video_path.exists()
        and project.section_start is None
        and project.section_end is None
    ):
        # Cut before sections were recorded: the chat offset is unknown.
        raise ValueError(
            "video.mp4 is a section cut whose bounds were not recorded; "
            f"delete {project.project_path} and re-run with the same "
            "--start/--to plus --chat"
        )
    download_live_chat(project.source_url, project.live_chat_raw_path)
    parse_live_chat(
        project.live_chat_raw_path,
        section_start=project.section_start,
        section_end=project.section_end,
    ).write(project.live_chat_messages_path)


def translate_project_live_chat(
    project: Project, progress: NoopProgressReporter
) -> None:
    """Translate chat against the finalized subtitles and pre-pass."""
    translate_live_chat(
        ChatTranslationInputs(
            messages_path=project.live_chat_messages_path,
            pre_pass_path=project.pre_pass_path,
            source_srt_path=project.srt_path,
            finalized_srt_path=project.finalized_srt_path,
            batches_dir=project.live_chat_batches_dir,
            polish_path=project.live_chat_polish_path,
            output_path=project.chat_translated_path,
        ),
        on_cost=lambda cost: project.add_cost("gemini", cost),
        progress=progress,
    )
