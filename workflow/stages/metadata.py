"""Metadata-fetching workflow stage."""

from loguru import logger

from project import Project, VideoSource
from services.ytdlp import (
    get_abema_episode_talents,
    get_tver_broadcast_date_label,
    get_tver_episode_talents,
    get_video_info,
    resolve_broadcast_date,
)


def fetch_metadata(project: Project) -> None:
    video_data = get_video_info(project.source_url)
    project.update_from_video_info(video_data)

    broadcast_date_label: str | None = None
    if project.source == VideoSource.TVER:
        talents = get_tver_episode_talents(project.id)
        if talents:
            project.update_from_source_talents(talents)
        broadcast_date_label = get_tver_broadcast_date_label(project.id)
        if broadcast_date_label:
            project.update_from_source_broadcast_date_label(
                broadcast_date_label
            )
    if project.source == VideoSource.ABEMA:
        talents = get_abema_episode_talents(project.id)
        if talents:
            project.update_from_source_talents(talents)

    broadcast_date = resolve_broadcast_date(
        source=project.source,
        video_id=project.id,
        video_info=video_data,
        tver_broadcast_date_label=broadcast_date_label,
    )
    if broadcast_date is not None:
        logger.info(f"Resolved broadcast date: {broadcast_date:%Y-%m-%d}")
        project.update_broadcast_date(broadcast_date)
    else:
        logger.warning(
            "Broadcast date could not be resolved; "
            "deliverables will use the undated name"
        )
