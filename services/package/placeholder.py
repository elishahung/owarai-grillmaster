"""Placeholder clip rotation for remix packaging.

`<PACKAGE_PATH>/placeholder` holds `001.*`, `002.*`, … clips and a
`state.json` cursor. Every remix deliverable takes the next clip in the
rotation and carries it as `judge.<ext>`.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field, ValidationError

from services.package.constants import (
    PLACEHOLDER_DIR_NAME,
    PLACEHOLDER_OUTPUT_STEM,
    PLACEHOLDER_STATE_FILE_NAME,
)
from services.package.errors import RemixPackageError


class PlaceholderState(BaseModel):
    """Which placeholder clip the next remix should take."""

    next_index: int = Field(default=1, ge=1)


def copy_placeholder(package_root: Path, target_dir: Path) -> Path | None:
    """Copy the next placeholder clip into `target_dir`.

    Returns `None` when the package root carries no `placeholder` folder —
    the clip is an opt-in extra, not a requirement of remix packaging.

    The cursor is advanced before the copy, not after: like the noise
    reservation, drawing a clip is a commitment, so concurrent packaging
    runs never ship the same placeholder.
    """
    placeholder_dir = package_root / PLACEHOLDER_DIR_NAME
    if not placeholder_dir.is_dir():
        return None

    sources = _placeholder_sources(placeholder_dir)
    index = _read_placeholder_state(placeholder_dir).next_index
    if index > len(sources):
        index = 1
    source = sources[index - 1]
    _write_placeholder_state(
        placeholder_dir, index + 1 if index < len(sources) else 1
    )

    target = target_dir / f"{PLACEHOLDER_OUTPUT_STEM}{source.suffix}"
    shutil.copy2(source, target)
    logger.info(f"Copied package placeholder: {source} -> {target}")
    return target


def _placeholder_sources(placeholder_dir: Path) -> list[Path]:
    """Collect `001.*`, `002.*`, … clips in index order."""
    sources = sorted(
        (
            path
            for path in placeholder_dir.iterdir()
            if path.is_file()
            and path.stem.isdigit()
            and len(path.stem) == 3
        ),
        key=lambda path: path.stem,
    )
    if not sources:
        raise RemixPackageError(
            f"no placeholder clips found: {placeholder_dir}"
        )
    expected_stems = [f"{index:03d}" for index in range(1, len(sources) + 1)]
    if [path.stem for path in sources] != expected_stems:
        raise RemixPackageError(
            f"placeholder clips must be contiguous 001..N: {placeholder_dir}"
        )
    return sources


def _read_placeholder_state(placeholder_dir: Path) -> PlaceholderState:
    state_path = placeholder_dir / PLACEHOLDER_STATE_FILE_NAME
    if not state_path.exists():
        return PlaceholderState()
    try:
        return PlaceholderState.model_validate_json(
            state_path.read_text(encoding="utf-8")
        )
    except (ValidationError, json.JSONDecodeError) as e:
        raise RemixPackageError(
            f"invalid placeholder state: {state_path}"
        ) from e


def _write_placeholder_state(placeholder_dir: Path, next_index: int) -> None:
    """Persist the cursor for the next remix run."""
    state_path = placeholder_dir / PLACEHOLDER_STATE_FILE_NAME
    state_path.write_text(
        PlaceholderState(next_index=next_index).model_dump_json(indent=2),
        encoding="utf-8",
    )
