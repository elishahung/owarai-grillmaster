"""`grill reset`: explicit, user-requested invalidation of stage results.

Clearing a stage removes its ledger entry, the state fields its
`StageDef.clear_state` resets, its whole `work/NN_<stage>/` directory and the
deliverables its `StageDef.outputs` declares; there is no other list to keep
in sync. The state is saved first, so an interrupted deletion still reruns
the stage.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

from grillmaster.core.stage_key import StageKey
from grillmaster.pipeline.registry import PIPELINE
from grillmaster.project.store import save_state

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from grillmaster.pipeline.registry import Pipeline
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState


@dataclass(frozen=True, slots=True)
class ResetResult:
    stages: tuple[StageKey, ...]
    removed: tuple[Path, ...]


def stages_from(key: StageKey) -> tuple[StageKey, ...]:
    """`key` and every later stage (`--from`)."""
    return tuple(stage for stage in StageKey if stage.number >= key.number)


def reset(
    layout: ProjectLayout,
    state: ProjectState,
    keys: Sequence[StageKey],
    *,
    pipeline: Pipeline = PIPELINE,
) -> ResetResult:
    """Clear `keys`, registered or not, so files and ledger entries of stages
    without a definition yet are removed too."""
    keys = tuple(keys)
    for key in keys:
        if (stage := pipeline.stage(key)) is not None:
            stage.clear_state(state)
    state.clear(keys)
    save_state(layout, state)
    removed = tuple(
        path for key in keys for path in _paths(layout, key, pipeline) if _remove(path)
    )
    return ResetResult(keys, removed)


def _paths(layout: ProjectLayout, key: StageKey, pipeline: Pipeline) -> list[Path]:
    stage = pipeline.stage(key)
    return [layout.work_dir(key), *(stage.outputs(layout) if stage else ())]


def _remove(path: Path) -> bool:
    if path.is_dir():
        shutil.rmtree(path)
        return True
    if path.exists():
        path.unlink()
        return True
    return False
