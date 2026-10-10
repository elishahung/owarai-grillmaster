"""Post-translation workflow stages."""

from grillmaster.project import Project
from grillmaster.services.finalize import finalize_and_export
from grillmaster.services.postprocess import (
    glossary_check_subtitles,
    refine_subtitles,
)


def refine_project_subtitles(project: Project) -> None:
    refine_subtitles(project)


def glossary_check_project_subtitles(project: Project) -> None:
    glossary_check_subtitles(project)


def finalize_project_subtitles(project: Project) -> None:
    finalize_and_export(
        project.glossary_checked_srt_path,
        project.ass_path,
        finalized_srt_path=project.finalized_srt_path,
        pre_pass_path=project.pre_pass_path,
    )
