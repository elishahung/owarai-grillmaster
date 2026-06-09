"""Prepared-noise chunk selection and state management."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from services.package.constants import (
    MIN_REMIX_CHUNK_COUNT,
    NOISE_STATE_FILE_NAME,
)
from services.package.errors import RemixPackageError


class NoiseState(BaseModel):
    next_index: int = Field(default=0, ge=0)


@dataclass(frozen=True)
class NoiseSelection:
    chunk_paths: list[Path]
    next_index: int


def select_noise_chunks(noise_dir: Path) -> NoiseSelection:
    """Select prepared chunks and compute the next cyclic index."""
    chunk_paths = _prepared_noise_chunks(noise_dir)
    if len(chunk_paths) < MIN_REMIX_CHUNK_COUNT:
        raise RemixPackageError(
            f"noise '{noise_dir.name}' needs at least "
            f"{MIN_REMIX_CHUNK_COUNT} chunks, found {len(chunk_paths)}"
        )

    state = _read_noise_state(noise_dir)
    start = state.next_index % len(chunk_paths)
    selected = [
        chunk_paths[(start + offset) % len(chunk_paths)]
        for offset in range(MIN_REMIX_CHUNK_COUNT)
    ]
    next_index = (start + MIN_REMIX_CHUNK_COUNT) % len(chunk_paths)
    return NoiseSelection(chunk_paths=selected, next_index=next_index)


def write_noise_state(noise_dir: Path, next_index: int) -> None:
    """Persist the next chunk index after successful remix packaging."""
    state_path = noise_dir / NOISE_STATE_FILE_NAME
    state_path.write_text(
        NoiseState(next_index=next_index).model_dump_json(indent=2),
        encoding="utf-8",
    )


def _prepared_noise_chunks(noise_dir: Path) -> list[Path]:
    if not noise_dir.exists():
        raise RemixPackageError(f"prepared noise folder not found: {noise_dir}")
    chunk_paths = sorted(
        path
        for path in noise_dir.glob("*.mp4")
        if path.stem.isdigit() and len(path.stem) == 3 and path.is_file()
    )
    expected_names = [f"{index:03d}.mp4" for index in range(len(chunk_paths))]
    actual_names = [path.name for path in chunk_paths]
    if actual_names != expected_names:
        raise RemixPackageError(
            f"prepared noise chunks must be contiguous 000..N: {noise_dir}"
        )
    return chunk_paths


def _read_noise_state(noise_dir: Path) -> NoiseState:
    state_path = noise_dir / NOISE_STATE_FILE_NAME
    if not state_path.exists():
        return NoiseState()
    try:
        return NoiseState.model_validate_json(
            state_path.read_text(encoding="utf-8")
        )
    except (ValidationError, json.JSONDecodeError) as e:
        raise RemixPackageError(f"invalid noise state: {state_path}") from e
