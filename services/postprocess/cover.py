"""Codex-driven cover image stylization from `poster.jpg`."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from project import Project
from settings import settings
from services.inference import AgentBackend, run_inference


_PROMPT = (Path(__file__).parent / "prompts" / "cover.md").read_text(
    encoding="utf-8"
)


class CoverFileMissingError(RuntimeError):
    """Raised when Codex finishes without producing the expected cover file."""


def generate_cover(project: Project) -> None:
    """Run Codex cover generation. Raises on missing input or output."""
    if project.poster_cover_path.exists():
        logger.info(
            f"Cover image already exists, skipping Codex invocation: "
            f"{project.poster_cover_path}"
        )
        return

    if not project.poster_path.exists():
        raise CoverFileMissingError(
            f"poster.jpg missing for project {project.id}: {project.poster_path}"
        )

    # Cover is image generation; only the Codex backend can produce a raster
    # image, so it is hardcoded here regardless of the global subtitle backend.
    logger.info(f"Invoking Codex for cover image generation: {project.id}")
    run_inference(
        backend=AgentBackend.CODEX,
        prompt=_PROMPT,
        cwd=project.project_path,
        images=[project.poster_path],
        reasoning_effort=settings.agent_postprocess_model.reasoning_effort,
    )

    if not project.poster_cover_path.exists():
        raise CoverFileMissingError(
            f"Codex did not produce cover image: {project.poster_cover_path}"
        )

    logger.info(f"Cover image generated: {project.poster_cover_path}")
