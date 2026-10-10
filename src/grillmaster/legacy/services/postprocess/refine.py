"""Agent-driven Traditional Chinese subtitle refinement."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from grillmaster.legacy.project import Project
from grillmaster.legacy.settings import settings
from grillmaster.legacy.services.inference import Backend, run_inference
from grillmaster.legacy.services.inference.tools import (
    build_refine_frame_tool_instruction,
)
from ._srt_guard import (
    parse_srt_file as _parse_srt,
    validate_srt_against_source as _validate_refined_srt,
)


_PROMPT = (Path(__file__).parent / "prompts" / "refine.md").read_text(encoding="utf-8")


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
            f"Discarding refined SRT from an unfinished run: {project.refined_srt_path}"
        )
        project.refined_srt_path.unlink()

    project.refine_cache_dir.mkdir(parents=True, exist_ok=True)

    spec = settings.agent_postprocess_model
    backend = Backend(spec.backend)
    logger.info(f"Invoking {backend.value} for subtitle refinement: {project.id}")
    # The on-demand frame tool's stage-specific wrapper writes into
    # `.refine/extra_frames`; window = the whole video.
    prompt = _PROMPT
    program_instruction = project.program_rules().render_instruction("refine")
    if program_instruction:
        prompt += "\n\n" + program_instruction
    prompt += "\n\n" + build_refine_frame_tool_instruction(project.project_path)
    run_inference(
        backend=backend,
        prompt=prompt,
        cwd=project.project_path,
        model=spec.model,
        reasoning_effort=spec.reasoning_effort,
    )

    if not project.refined_srt_path.exists():
        raise RefinementValidationError(
            f"agent did not produce refined SRT: {project.refined_srt_path}"
        )

    errors = _validate_refined_srt(project.translated_path, project.refined_srt_path)
    if errors:
        raise RefinementValidationError(
            "refined SRT failed structural validation:\n" + "\n".join(errors)
        )

    logger.info(
        f"Refined SRT validated: {len(_parse_srt(project.refined_srt_path))} blocks"
    )

    if not project.refine_report_path.exists():
        logger.warning(
            f"Refinement report missing (expected at {project.refine_report_path})"
        )
