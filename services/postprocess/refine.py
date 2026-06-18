"""Codex-driven Traditional Chinese subtitle refinement."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from project import Project
from settings import settings
from services.inference import AgentBackend, run_inference
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
    """Run Codex refinement and structurally validate the output."""
    if project.refined_srt_path.exists():
        logger.info(
            f"Refined SRT already exists, skipping Codex invocation: "
            f"{project.refined_srt_path}"
        )
        return

    if not project.translated_path.exists():
        raise RefinementValidationError(
            f"translated SRT missing before refinement: {project.translated_path}"
        )

    project.refine_cache_dir.mkdir(parents=True, exist_ok=True)

    backend = AgentBackend(settings.agent_postprocess_backend)
    logger.info(
        f"Invoking {backend.value} for subtitle refinement: {project.id}"
    )
    spec = settings.agent_postprocess_model
    run_inference(
        backend=backend,
        prompt=_PROMPT,
        cwd=project.project_path,
        model=spec.model,
        reasoning_effort=spec.reasoning_effort,
    )

    if not project.refined_srt_path.exists():
        raise RefinementValidationError(
            f"Codex did not produce refined SRT: {project.refined_srt_path}"
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
