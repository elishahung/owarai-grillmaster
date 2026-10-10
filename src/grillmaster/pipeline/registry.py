"""The one list of stages, side tasks and delivery steps.

`STAGES` must follow `StageKey` declaration order (asserted when this module
loads, by building `PIPELINE`). Stage ports append their definitions here as
`stages/<key>.py` modules land; a key without a definition simply does not
run yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from grillmaster.events.types import PlanEntry, PlanKind
from grillmaster.pipeline.delivery import archive_entry
from grillmaster.stages import (
    asr,
    audio,
    chat_fetch,
    chat_translate,
    chunks,
    combine,
    cover,
    date_research,
    download,
    finalize,
    glossary,
    metadata,
    package,
    prepass,
    refine,
    transcript,
)

if TYPE_CHECKING:
    from grillmaster.config.model import AppConfig
    from grillmaster.config.secrets import Secrets
    from grillmaster.core.stage_key import StageKey
    from grillmaster.project.state import ProjectState
    from grillmaster.stages.base import (
        DeliveryStepDef,
        RunOptions,
        SideTaskDef,
        StageDef,
    )


@dataclass(frozen=True, slots=True)
class Pipeline:
    """A validated set of definitions; tests build their own from fakes."""

    stages: tuple[StageDef, ...]
    side_tasks: tuple[SideTaskDef[Any], ...] = ()
    delivery: tuple[DeliveryStepDef, ...] = ()

    def __post_init__(self) -> None:
        numbers = [stage.key.number for stage in self.stages]
        if numbers != sorted(set(numbers)):
            order = ", ".join(stage.key for stage in self.stages)
            raise ValueError(
                f"Stages must be unique and in StageKey order, got: {order}"
            )
        registered = {stage.key for stage in self.stages}
        for task in self.side_tasks:
            if task.start_after not in registered:
                raise ValueError(
                    f"Side task {task.key} starts after unregistered stage "
                    f"{task.start_after}"
                )
        _check_unique("side task", [task.key for task in self.side_tasks])
        _check_unique("delivery step", [step.key for step in self.delivery])

    def stage(self, key: StageKey) -> StageDef | None:
        return next((stage for stage in self.stages if stage.key == key), None)

    def check(
        self,
        options: RunOptions,
        config: AppConfig,
        secrets: Secrets,
        *,
        state: ProjectState | None = None,
    ) -> None:
        """Reject a run that cannot finish, before anything runs: options this
        pipeline cannot honour, then the `preflight` of every stage the run
        would execute (enabled, not complete in `state`, up to
        `--break-after`)."""
        if options.break_after is not None and self.stage(options.break_after) is None:
            raise ValueError(
                f"--break-after {options.break_after}: that stage is not registered"
            )
        for stage in self.stages:
            if stage.enabled(options) and not (state and state.is_done(stage.key)):
                stage.preflight(config, secrets)
            if stage.key == options.break_after:
                return

    def plan(
        self, options: RunOptions, config: AppConfig, *, archive: bool = False
    ) -> tuple[PlanEntry, ...]:
        """Every step this run may execute, for `RunStarted`: stages, delivery,
        the archive move (when `archive` is wired), then side tasks.

        Side tasks and delivery are planned disabled on a `--break-after`
        run, which never reaches them.
        """
        complete = options.complete_run
        return (
            *(
                _entry(stage, PlanKind.STAGE, config, enabled=stage.enabled(options))
                for stage in self.stages
            ),
            *(
                _entry(
                    step,
                    PlanKind.DELIVERY,
                    config,
                    enabled=complete and step.enabled(options, config),
                )
                for step in self.delivery
            ),
            *((archive_entry(options),) if archive else ()),
            *(
                _entry(
                    task,
                    PlanKind.SIDE_TASK,
                    config,
                    enabled=complete and task.enabled(options, config),
                )
                for task in self.side_tasks
            ),
        )


def _entry(
    definition: StageDef | DeliveryStepDef | SideTaskDef[Any],
    kind: PlanKind,
    config: AppConfig,
    *,
    enabled: bool,
) -> PlanEntry:
    return PlanEntry(
        key=definition.key,
        label=definition.label,
        kind=kind,
        enabled=enabled,
        params=definition.params(config),
        weight=definition.weight,
    )


def _check_unique(kind: str, keys: list[str]) -> None:
    if duplicates := sorted({key for key in keys if keys.count(key) > 1}):
        raise ValueError(f"Duplicate {kind} keys: {', '.join(duplicates)}")


STAGES: tuple[StageDef, ...] = (
    metadata.STAGE,
    download.STAGE,
    combine.STAGE,
    chat_fetch.STAGE,
    audio.STAGE,
    asr.STAGE,
    transcript.STAGE,
    prepass.STAGE,
    chunks.STAGE,
    refine.STAGE,
    glossary.STAGE,
    finalize.STAGE,
    chat_translate.STAGE,
)
SIDE_TASKS: tuple[SideTaskDef[Any], ...] = (cover.TASK, date_research.TASK)
DELIVERY: tuple[DeliveryStepDef, ...] = (package.STEP,)

PIPELINE = Pipeline(STAGES, SIDE_TASKS, DELIVERY)
