"""ASR and source-SRT workflow stages."""

from loguru import logger

from project import Project
from services.elevenlabs import ElevenLabsASR, convert_file


def run_asr(project: Project) -> None:
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


def convert_asr_to_srt(project: Project) -> None:
    convert_file(project.asr_path, project.srt_path)
