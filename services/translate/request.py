"""Inputs required to run the translation pipeline."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class TranslationRequest(BaseModel):
    """Inputs required to run the translation pipeline (pre-pass + chunks)."""

    srt_path: Path
    video_path: Path
    audio_path: Path
    output_path: Path
    pre_pass_path: Path
    pre_pass_cache_dir: Path
    chunks_cache_dir: Path
    video_title: str | None = None
    video_description: str | None = None
    translation_hint: str | None = None
    # Rendered `config.json` program instructions for the stage being run.
    program_instruction: str | None = None
    source_metadata_context: str | None = None
    parent_pre_pass_context: str | None = None
    official_subtitle_path: Path | None = None
