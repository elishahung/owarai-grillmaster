"""Inserts: the next file of a media pool, copied into the deliverable.

The caller decides which inserts apply (the `[[package.inserts]]` rules for
this program and deliverable kind); each one copies its pool's next file as
`<output><ext>`. A declared pool that is missing or misnumbered, or holds a
file whose suffix exceeds the deliverable's path reserve, is a configuration
error and fails the package (`PoolError`) before any cursor moves.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.package.errors import PoolError
from grillmaster.package.pools import MediaPool

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

# Insert outputs are user-chosen stems that `config` caps at this length; a
# copy keeps its pool file's suffix, at most `INSERT_SUFFIX_ALLOWANCE` units
# (`.jpeg`, `.webm`). The deliverable's path reserve counts both.
INSERT_OUTPUT_MAX_LENGTH = 24
INSERT_SUFFIX_ALLOWANCE = 5


@dataclass(frozen=True, slots=True)
class Insert:
    """Copy the next file of pool `pool` as `<output><ext>`."""

    pool: str
    output: str

    def __post_init__(self) -> None:
        if len(self.output) > INSERT_OUTPUT_MAX_LENGTH:
            raise ValueError(
                f"insert output {self.output!r} is longer than "
                f"{INSERT_OUTPUT_MAX_LENGTH} characters"
            )


def copy_inserts(
    inserts: Sequence[Insert], *, package_root: Path, target_dir: Path
) -> list[Path]:
    """Copy each insert's next pool file into `target_dir`; returns the copies.

    Every pool is checked before the first cursor advances; each cursor
    advances before its copy (see `pools`).
    """
    pools = [MediaPool.under(package_root, insert.pool) for insert in inserts]
    for pool in pools:
        for path in pool.files():
            if len(path.suffix) > INSERT_SUFFIX_ALLOWANCE:
                raise PoolError(
                    f"Insert pool file suffix is longer than "
                    f"{INSERT_SUFFIX_ALLOWANCE} characters: {path}"
                )
    copies: list[Path] = []
    for insert, pool in zip(inserts, pools, strict=True):
        source = pool.next_file()
        target = target_dir / f"{insert.output}{source.suffix}"
        shutil.copy2(source, target)
        logger.info(f"Copied insert {insert.output}: {source} -> {target}")
        copies.append(target)
    return copies
