"""Inserts: the next file of a media pool, copied into the deliverable.

The caller decides which inserts apply (the `[[package.inserts]]` rules for
this program and deliverable kind); each one copies its pool's next file as
`<output><ext>`. A declared pool that is missing or misnumbered is a
configuration error and fails the package (`PoolError`).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.package.pools import MediaPool

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class Insert:
    """Copy the next file of pool `pool` as `<output><ext>`."""

    pool: str
    output: str


def copy_inserts(
    inserts: Sequence[Insert], *, package_root: Path, target_dir: Path
) -> list[Path]:
    """Copy each insert's next pool file into `target_dir`; returns the copies.

    Each pool's cursor advances before its copy (see `pools`).
    """
    copies: list[Path] = []
    for insert in inserts:
        source = MediaPool.under(package_root, insert.pool).next_file()
        target = target_dir / f"{insert.output}{source.suffix}"
        shutil.copy2(source, target)
        logger.info(f"Copied insert {insert.output}: {source} -> {target}")
        copies.append(target)
    return copies
