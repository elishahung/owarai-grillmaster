"""Delivery: the steps after the last stage (package), then the archive move.

Delivery steps are not stages: they have no ledger entry and run again on
every complete run. A `--break-after` run skips them. Archive is not a
`DeliveryStepDef`: it moves the project directory, so the runner performs it
last, after the project's logs are closed, and delivery steps always read the
local project (design §9.4, owner decision #32).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from grillmaster.events.types import PlanEntry, PlanKind, SkipReason, StepSkipped
from grillmaster.pipeline.stage import no_params
from grillmaster.pipeline.steps import execute_step

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from grillmaster.config.model import AppConfig
    from grillmaster.events.bus import EventSink
    from grillmaster.pipeline.logs import ProjectLogs
    from grillmaster.pipeline.stage import RunOptions, StageContext
    from grillmaster.pipeline.steps import UsageCollector
    from grillmaster.project.layout import ProjectLayout

ARCHIVE_KEY = "archive"
_ARCHIVE_LABEL = "Archive"
_ARCHIVE_WEIGHT = 1

type Archive = Callable[[ProjectLayout], ProjectLayout]


def always_deliver(_options: RunOptions, _config: AppConfig) -> bool:
    return True


@dataclass(frozen=True, slots=True)
class DeliveryStepDef:
    """One delivery step.

    `run` returns the `StepCompleted` result text; `workdir` is the step's
    own directory (its `StageContext.workdir`).
    """

    key: str
    label: str
    weight: int
    run: Callable[[StageContext], str | None]
    workdir: Callable[[ProjectLayout], Path]
    enabled: Callable[[RunOptions, AppConfig], bool] = always_deliver
    params: Callable[[AppConfig], dict[str, str]] = no_params


def run_delivery(
    steps: Sequence[DeliveryStepDef],
    *,
    layout: ProjectLayout,
    context: Callable[[Path], StageContext],
    options: RunOptions,
    config: AppConfig,
    events: EventSink,
    clock: Callable[[], float],
    usage: UsageCollector,
) -> None:
    """Run `steps` in order; `context` builds a step context for a workdir.

    A failing step raises after reporting `StepFailed`; later steps do not
    run.
    """
    for step in steps:
        reason = _skip_reason(step, options, config)
        if reason is not None:
            events.emit(StepSkipped(step.key, PlanKind.DELIVERY, reason))
            continue
        ctx = context(step.workdir(layout))
        execute_step(
            step.key,
            PlanKind.DELIVERY,
            lambda step=step, ctx=ctx: step.run(ctx),
            events=events,
            clock=clock,
            usage=usage,
            finish=lambda outcome: outcome.value,
        )


def _skip_reason(
    step: DeliveryStepDef, options: RunOptions, config: AppConfig
) -> SkipReason | None:
    if not options.complete_run:
        return SkipReason.BREAKPOINT
    if not step.enabled(options, config):
        return SkipReason.DISABLED
    return None


def archive_entry(options: RunOptions) -> PlanEntry:
    """The archive step's plan row; planned only when an archive is wired."""
    return PlanEntry(
        key=ARCHIVE_KEY,
        label=_ARCHIVE_LABEL,
        kind=PlanKind.DELIVERY,
        enabled=options.complete_run,
        weight=_ARCHIVE_WEIGHT,
    )


def run_archive(
    archive: Archive,
    layout: ProjectLayout,
    *,
    logs: ProjectLogs,
    options: RunOptions,
    events: EventSink,
    clock: Callable[[], float],
    usage: UsageCollector,
) -> ProjectLayout:
    """Move the project with `archive`; returns where it lives now.

    Closes the project's logs first (Windows cannot move a directory with
    open files), so from here on only live sinks see the run's events.
    """
    if not options.complete_run:
        events.emit(StepSkipped(ARCHIVE_KEY, PlanKind.DELIVERY, SkipReason.BREAKPOINT))
        return layout
    logs.close()
    outcome = execute_step(
        ARCHIVE_KEY,
        PlanKind.DELIVERY,
        lambda: archive(layout),
        events=events,
        clock=clock,
        usage=usage,
        finish=lambda outcome: str(outcome.value.root),
    )
    return outcome.value
