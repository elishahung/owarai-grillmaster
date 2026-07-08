"""Post-translation workflow stages."""

from project import Project
from services.finalize import finalize_and_export
from services.postprocess import (
    glossary_check_subtitles,
    refine_subtitles,
)


def refine_project_subtitles(project: Project) -> None:
    refine_subtitles(project)


def glossary_check_project_subtitles(project: Project) -> None:
    glossary_check_subtitles(project)


def finalize_project_subtitles(project: Project) -> None:
    if project.glossary_checked_srt_path.exists():
        srt_source = project.glossary_checked_srt_path
    elif project.refined_srt_path.exists():
        srt_source = project.refined_srt_path
    else:
        srt_source = project.translated_path
    finalize_and_export(
        srt_source,
        project.ass_path,
        finalized_srt_path=project.finalized_srt_path,
        pre_pass_path=project.pre_pass_path,
    )
