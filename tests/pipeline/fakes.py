"""Fake stage, side-task and delivery definitions for the pipeline tests."""

from __future__ import annotations

import itertools
from typing import TYPE_CHECKING

from tests.fakes import Recorder

from grillmaster.events.context import current_stage
from grillmaster.stages.base import DeliveryStepDef, SideTaskDef, StageDef, no_clear

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from grillmaster.config.model import AppConfig
    from grillmaster.core.stage_key import SideTaskKey, StageKey
    from grillmaster.events.types import SkipReason
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState
    from grillmaster.stages.base import RunOptions, StageContext


class StageFailedError(RuntimeError):
    pass


class Journal(Recorder[str]):
    """What the fake definitions did, in order; thread-safe."""

    @property
    def entries(self) -> list[str]:
        return self.items


def ticking_clock(step: float = 1.0) -> Callable[[], float]:
    """A clock advancing `step` per call, so each step's elapsed is `step`."""
    counter = itertools.count()
    return lambda: next(counter) * step


def fake_stage(
    key: StageKey,
    journal: Journal,
    *,
    action: Callable[[StageContext], str | None] | None = None,
    enabled: bool = True,
    outputs: Callable[[ProjectLayout], Sequence[Path]] = lambda layout: (),
    params: dict[str, str] | None = None,
    clear_state: Callable[[ProjectState], None] = no_clear,
) -> StageDef:
    def run(ctx: StageContext) -> str | None:
        journal.add(f"run:{key}@{current_stage()}")
        return None if action is None else action(ctx)

    def on_skip(ctx: StageContext) -> None:
        journal.add(f"on_skip:{key}")

    return StageDef(
        key=key,
        label=f"Stage {key}",
        weight=key.number,
        run=run,
        outputs=outputs,
        enabled=lambda options: enabled,
        on_skip=on_skip,
        params=lambda config: dict(params or {}),
        clear_state=clear_state,
    )


def failing(message: str) -> Callable[[StageContext], None]:
    def action(ctx: StageContext) -> None:
        raise StageFailedError(message)

    return action


def fake_side_task(
    key: SideTaskKey,
    start_after: StageKey,
    journal: Journal,
    *,
    action: Callable[[StageContext], str | None] | None = None,
    enabled: bool = True,
) -> SideTaskDef[str | None]:
    def run(ctx: StageContext) -> str | None:
        journal.add(f"side:{key}@{current_stage()}")
        return action(ctx) if action is not None else None

    def on_skip(ctx: StageContext, reason: SkipReason) -> None:
        journal.add(f"side_skip:{key}:{reason}")

    def is_enabled(options: RunOptions, config: AppConfig) -> bool:
        return enabled

    return SideTaskDef(
        key=key,
        label=f"Side {key}",
        weight=2,
        start_after=start_after,
        run=run,
        enabled=is_enabled,
        on_skip=on_skip,
    )


def fake_delivery(
    key: str,
    journal: Journal,
    *,
    action: Callable[[StageContext], str | None] | None = None,
    enabled: bool = True,
) -> DeliveryStepDef:
    """A delivery step working in `work/<key>/`."""

    def run(ctx: StageContext) -> str | None:
        journal.add(f"deliver:{key}@{current_stage()}")
        return action(ctx) if action is not None else None

    def is_enabled(options: RunOptions, config: AppConfig) -> bool:
        return enabled

    return DeliveryStepDef(
        key=key,
        label=f"Deliver {key}",
        weight=3,
        run=run,
        workdir=lambda layout: layout.work_root / key,
        enabled=is_enabled,
    )
