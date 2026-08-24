"""Agent-driven Traditional Chinese subtitle refinement."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from project import Project
from settings import settings
from services.media import MediaProcessor
from services.inference import Backend, is_agent_backend, run_inference
from services.inference.tools import build_refine_frame_tool_instruction
from ._srt_guard import (
    parse_srt_file as _parse_srt,
    validate_srt_against_source as _validate_refined_srt,
)


_PROMPT = (Path(__file__).parent / "prompts" / "refine.md").read_text(
    encoding="utf-8"
)


class RefinementValidationError(RuntimeError):
    """Raised when the refined SRT structurally diverges from the source."""


def refine_subtitles(project: Project) -> None:
    """Run agent refinement and structurally validate the output."""
    if not project.translated_path.exists():
        raise RefinementValidationError(
            f"translated SRT missing before refinement: {project.translated_path}"
        )

    if project.refined_srt_path.exists():
        # `is_srt_refined` in project.json is the only completion marker, and
        # the workflow skips this stage entirely once it is set. Reaching here
        # means the previous attempt did NOT finish (agent timeout, crash,
        # failed validation), so the file is that attempt's half-written
        # output — discard it and refine again.
        logger.warning(
            f"Discarding refined SRT from an unfinished run: "
            f"{project.refined_srt_path}"
        )
        project.refined_srt_path.unlink()

    project.refine_cache_dir.mkdir(parents=True, exist_ok=True)

    spec = settings.agent_postprocess_model
    backend = Backend(spec.backend)
    logger.info(
        f"Invoking {backend.value} for subtitle refinement: {project.id}"
    )
    # Offer the on-demand frame tool only to a backend that can run it. The
    # stage-specific wrapper writes into `.refine/extra_frames`; window = the
    # whole video.
    prompt = _PROMPT
    if is_agent_backend(backend):
        try:
            video_end = MediaProcessor.get_media_duration(project.video_path)
        except Exception:
            video_end = 0.0
        prompt += "\n\n" + build_refine_frame_tool_instruction(
            project.project_path,
            0.0,
            video_end,
        )
    run_inference(
        backend=backend,
        prompt=prompt,
        cwd=project.project_path,
        model=spec.model,
        reasoning_effort=spec.reasoning_effort,
        web_search=is_agent_backend(backend),
    )

    if not project.refined_srt_path.exists():
        raise RefinementValidationError(
            f"agent did not produce refined SRT: {project.refined_srt_path}"
        )

    errors = _validate_refined_srt(
        project.translated_path, project.refined_srt_path
    )
    if errors:
        raise RefinementValidationError(
            "refined SRT failed structural validation:\n" + "\n".join(errors)
        )

    logger.info(
        f"Refined SRT validated: "
        f"{len(_parse_srt(project.refined_srt_path))} blocks"
    )

    if not project.refine_report_path.exists():
        logger.warning(
            f"Refinement report missing (expected at "
            f"{project.refine_report_path})"
        )
