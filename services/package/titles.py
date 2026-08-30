"""Traditional Chinese title suggestions for a deliverable.

Titles are a packaging artifact, not a pipeline stage: while packaging, the
source project's `pre_pass.json` is handed to `settings.agent_common_model`,
which returns three candidate titles with a one-line rationale each. The
result is cached in the source project as `.titles/titles.json` (delete the
file to force a re-run) and copied into the package directory.
"""
from __future__ import annotations

from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field

from project import (
    PRE_PASS_CACHE_DIR_NAME,
    PRE_PASS_FILE_NAME,
    TITLES_DIR_NAME,
    TITLES_FILE_NAME,
)
from services.inference import Backend, run_inference
from settings import settings

_PROMPT = (Path(__file__).parent / "prompts" / "titles.md").read_text(
    encoding="utf-8"
)

# Fixed count — the deliverable always offers exactly three alternatives.
TITLE_COUNT = 3


class TitleSuggestion(BaseModel):
    """One candidate title with the reason it was chosen."""

    # The length bound reaches the model through the generated JSON Schema,
    # so an over-long title is repaired by the schema-enforcement loop rather
    # than shipped.
    title: str = Field(min_length=2, max_length=8)
    reason: str


class TitleSuggestions(BaseModel):
    """Schema-enforced verdict of the title-suggestion agent."""

    titles: list[TitleSuggestion] = Field(
        min_length=TITLE_COUNT, max_length=TITLE_COUNT
    )


def titles_path(source_root: Path) -> Path:
    """Path of the cached title suggestions inside a project directory."""
    return source_root / TITLES_DIR_NAME / TITLES_FILE_NAME


def load_titles(source_root: Path) -> TitleSuggestions | None:
    """Load the cached suggestions; an unreadable file counts as a miss.

    Treating a corrupt artifact as a miss (with a warning) lets the agent
    re-run and overwrite it instead of wedging every package run on the same
    parse error.
    """
    path = titles_path(source_root)
    if not path.exists():
        return None
    try:
        return TitleSuggestions.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (ValueError, OSError) as error:
        logger.warning(
            f"Ignoring unreadable title suggestions ({path}): {error}"
        )
        return None


def generate_titles(source_root: Path) -> TitleSuggestions:
    """Derive title suggestions from the project's pre-pass briefing."""
    pre_pass = source_root / PRE_PASS_CACHE_DIR_NAME / PRE_PASS_FILE_NAME
    if not pre_pass.exists():
        raise FileNotFoundError(
            f"pre-pass JSON not found, cannot suggest titles: {pre_pass}"
        )

    spec = settings.agent_common_model
    backend = Backend(spec.backend)
    logger.info(
        f"Invoking {backend.value} for title suggestions: {source_root}"
    )
    prompt = (
        _PROMPT
        + "\n\n## pre_pass.json\n\n```json\n"
        + pre_pass.read_text(encoding="utf-8")
        + "\n```\n"
    )
    # cwd is deliberately None (throwaway temp dir): the whole input is the
    # pre-pass content injected above, and this task must not touch project
    # files — Python owns writing titles.json.
    inference = run_inference(
        backend=backend,
        prompt=prompt,
        schema=TitleSuggestions,
        model=spec.model,
        reasoning_effort=spec.reasoning_effort,
    )
    result = TitleSuggestions.model_validate_json(inference.text)

    path = titles_path(source_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.model_dump_json(indent=4), encoding="utf-8")
    logger.info(f"Title suggestions saved: {path}")
    return result


def ensure_titles(source_root: Path) -> Path | None:
    """Make sure `source_root` has title suggestions before it is packaged.

    Returns the artifact path, or None when nothing is available. Existing
    suggestions are reused as-is; a missing one is generated when
    ENABLE_PACKAGE_TITLE_SUGGESTION is on. Best-effort like the rest of
    packaging: a failed generation warns and never breaks the deliverable.
    """
    path = titles_path(source_root)
    if load_titles(source_root) is not None:
        logger.info(
            f"Title suggestions already exist, skipping agent invocation: "
            f"{path}"
        )
        return path

    if not settings.enable_package_title_suggestion:
        logger.info(
            "Package title suggestion disabled; packaging without titles.json"
        )
        return None

    try:
        generate_titles(source_root)
    except Exception as error:
        logger.warning(f"Title suggestion failed: {error}")
        return None
    return path
