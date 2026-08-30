"""Noise cut selection and cursor state for remix packaging."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from services.media import MediaProcessor, NoiseCut
from services.package.constants import (
    NOISE_CUT_DURATION_SECONDS,
    NOISE_CUTS_PER_SEGMENT,
    NOISE_STATE_FILE_NAME,
)
from services.package.errors import RemixPackageError


class NoiseState(BaseModel):
    """Where the next remix should resume inside the noise sources."""

    next_index: int = Field(default=0, ge=0)
    next_seconds: int = Field(default=0, ge=0)


@dataclass(frozen=True)
class NoiseSelection:
    cuts: list[NoiseCut]
    next_index: int
    next_seconds: int


def reserve_noise_cuts(
    noise_dir: Path,
    cut_count: int = NOISE_CUTS_PER_SEGMENT,
    cut_duration: int = NOISE_CUT_DURATION_SECONDS,
) -> NoiseSelection:
    """Walk the noise sources in seconds and reserve `cut_count` slices.

    Each cut is `cut_duration` long, except that a source whose remainder
    would be shorter than one full cut is consumed to its end in the same
    cut. Sources are walked in index order and wrap back to index 0.

    The advanced cursor is persisted before returning, not after the cuts are
    rendered: concurrent packaging runs must not draw the same noise, so a
    reservation is a commitment. A run that then fails simply skips its noise.
    """
    if cut_count <= 0:
        raise ValueError("cut_count must be positive")
    if cut_duration <= 0:
        raise ValueError("cut_duration must be positive")

    sources = _noise_sources(noise_dir)
    durations: dict[int, float] = {}

    def duration_of(index: int) -> float:
        if index not in durations:
            durations[index] = MediaProcessor.get_media_duration(sources[index])
        return durations[index]

    state = _read_noise_state(noise_dir)
    index = state.next_index % len(sources)
    start = float(state.next_seconds)

    cuts: list[NoiseCut] = []
    for _ in range(cut_count):
        skipped = 0
        while duration_of(index) - start <= 0:
            index = (index + 1) % len(sources)
            start = 0.0
            skipped += 1
            if skipped > len(sources):
                raise RemixPackageError(
                    f"no usable noise source in {noise_dir}"
                )
        remaining = duration_of(index) - start
        if remaining < 2 * cut_duration:
            cuts.append(
                NoiseCut(
                    source=sources[index],
                    start_seconds=start,
                    duration_seconds=remaining,
                )
            )
            index = (index + 1) % len(sources)
            start = 0.0
        else:
            cuts.append(
                NoiseCut(
                    source=sources[index],
                    start_seconds=start,
                    duration_seconds=float(cut_duration),
                )
            )
            start += cut_duration

    next_seconds = int(start)
    _write_noise_state(noise_dir, index, next_seconds)
    return NoiseSelection(
        cuts=cuts, next_index=index, next_seconds=next_seconds
    )


def _write_noise_state(
    noise_dir: Path, next_index: int, next_seconds: int
) -> None:
    """Persist the cursor for the next remix run."""
    state_path = noise_dir / NOISE_STATE_FILE_NAME
    state_path.write_text(
        NoiseState(
            next_index=next_index, next_seconds=next_seconds
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )


def _noise_sources(noise_dir: Path) -> list[Path]:
    """Collect `000.*`, `001.*`, … source videos in index order."""
    if not noise_dir.exists():
        raise RemixPackageError(f"noise folder not found: {noise_dir}")
    sources = sorted(
        (
            path
            for path in noise_dir.iterdir()
            if path.is_file()
            and path.stem.isdigit()
            and len(path.stem) == 3
        ),
        key=lambda path: path.stem,
    )
    if not sources:
        raise RemixPackageError(f"no noise sources found: {noise_dir}")
    expected_stems = [f"{index:03d}" for index in range(len(sources))]
    if [path.stem for path in sources] != expected_stems:
        raise RemixPackageError(
            f"noise sources must be contiguous 000..N: {noise_dir}"
        )
    return sources


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
