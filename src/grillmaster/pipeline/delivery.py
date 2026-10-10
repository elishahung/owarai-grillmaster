"""Delivery: the archive move after the last stage, then the delivery steps
(package).

Delivery steps are not stages: they have no ledger entry and run again on
every complete run. A `--break-after` run skips them and the archive. Archive
is not a `DeliveryStepDef`: it moves the project directory, so the runner
performs it as soon as the stages (and side tasks) are done, and the delivery
steps then read the project where it now lives, the archived copy when an
archive is configured.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.events.types import PlanEntry, PlanKind, SkipReason, StepSkipped
from grillmaster.pipeline.steps import execute_step

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from grillmaster.config.model import AppConfig
    from grillmaster.events.bus import EventSink
    from grillmaster.pipeline.logs import ProjectLogs
    from grillmaster.pipeline.steps import UsageCollector
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.stages.base import DeliveryStepDef, RunOptions, StageContext

ARCHIVE_KEY = "archive"
_ARCHIVE_LABEL = "Archive"
_ARCHIVE_WEIGHT = 1

type Archive = Callable[[ProjectLayout], ProjectLayout]


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

    The project's logs close for the move (Windows cannot move a directory
    with open files) and reopen, appending, wherever the project then lives:
    the new location, or the old one when the move failed. `StepStarted` is
    thus written before the move and the outcome after it; only the log lines
    of the move itself reach just the live sinks.
    """
    if not options.complete_run:
        events.emit(StepSkipped(ARCHIVE_KEY, PlanKind.DELIVERY, SkipReason.BREAKPOINT))
        return layout

    def move() -> ProjectLayout:
        logs.close()
        try:
            moved = archive(layout)
        except BaseException:
            logs.relocate(layout)
            raise
        logs.relocate(moved)
        return moved

    outcome = execute_step(
        ARCHIVE_KEY,
        PlanKind.DELIVERY,
        move,
        events=events,
        clock=clock,
        usage=usage,
        finish=lambda outcome: str(outcome.value.root),
    )
    return outcome.value
