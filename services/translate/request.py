"""Inputs required to run the translation pipeline."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class TranslationRequest(BaseModel):
    """Inputs required to run the translation pipeline (pre-pass + chunks)."""

    video_description: str | None
    srt_path: Path
    audio_key: str
    video_path: Path
    audio_path: Path
    output_path: Path
    pre_pass_path: Path
    pre_pass_cache_dir: Path
    chunks_cache_dir: Path
    source_metadata_context: str | None = None
    parent_pre_pass_context: str | None = None
