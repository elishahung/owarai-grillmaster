"""Pre-pass and chunk translation workflow stages."""

from project import Project
from services.program_config import InstructionStep
from services.progress import NoopProgressReporter
from services import translate
from services.translate import TranslationRequest


def make_translation_request(
    project: Project, step: InstructionStep
) -> TranslationRequest:
    """Build the request for the pre-pass or chunk stage (`step`)."""
    return TranslationRequest(
        video_title=project.source_metadata.title,
        video_description=project.source_metadata.description,
        translation_hint=project.translation_hint,
        program_instruction=project.program_rules().render_instruction(step),
        srt_path=project.srt_path,
        video_path=project.video_path,
        audio_path=project.audio_path,
        output_path=project.translated_path,
        pre_pass_path=project.pre_pass_path,
        pre_pass_cache_dir=project.pre_pass_cache_dir,
        chunks_cache_dir=project.chunks_cache_dir,
        source_metadata_context=project.source_metadata_context(),
        parent_pre_pass_context=project.parent_pre_pass_context(),
        official_subtitle_path=(
            project.official_subtitle_path
            if project.official_subtitle_path.exists()
            else None
        ),
    )


def run_pre_pass(project: Project) -> None:
    translate.run_pre_pass(make_translation_request(project, "pre_pass"))


def translate_chunks(project: Project, progress: NoopProgressReporter) -> None:
    translate.translate_chunks(
        make_translation_request(project, "translate"), progress=progress
    )
