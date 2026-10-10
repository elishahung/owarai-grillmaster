from __future__ import annotations

import inspect
import re
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from tests.pipeline.fakes import Journal, fake_delivery, fake_side_task, fake_stage

from grillmaster.core.stage_key import SideTaskKey, StageKey
from grillmaster.events.types import PlanEntry, PlanKind
from grillmaster.pipeline.registry import STAGES, Pipeline
from grillmaster.pipeline.stage import no_clear

if TYPE_CHECKING:
    from grillmaster.config.load import LoadedConfig
    from grillmaster.pipeline.stage import RunOptions, StageDef


def test_out_of_order_stages_are_rejected():
    journal = Journal()
    with pytest.raises(ValueError, match="StageKey order"):
        Pipeline(
            (
                fake_stage(StageKey.DOWNLOAD, journal),
                fake_stage(StageKey.METADATA, journal),
            )
        )


def test_duplicate_stages_are_rejected():
    journal = Journal()
    with pytest.raises(ValueError, match="unique"):
        Pipeline((fake_stage(StageKey.ASR, journal), fake_stage(StageKey.ASR, journal)))


def test_side_task_needs_a_registered_start_stage():
    journal = Journal()
    with pytest.raises(ValueError, match="unregistered stage download"):
        Pipeline(
            (fake_stage(StageKey.METADATA, journal),),
            side_tasks=(fake_side_task(SideTaskKey.COVER, StageKey.DOWNLOAD, journal),),
        )


def test_duplicate_delivery_keys_are_rejected():
    journal = Journal()
    with pytest.raises(ValueError, match="Duplicate delivery step keys: package"):
        Pipeline(
            (),
            delivery=(
                fake_delivery("package", journal),
                fake_delivery("package", journal),
            ),
        )


def test_stage_weight_must_be_positive():
    stage = fake_stage(StageKey.ASR, Journal())
    with pytest.raises(ValueError, match="weight"):
        replace(stage, weight=0)


def test_plan_lists_stages_delivery_archive_then_side_tasks(
    options: RunOptions, loaded: LoadedConfig
):
    journal = Journal()
    pipeline = Pipeline(
        (
            fake_stage(StageKey.METADATA, journal, params={"official_cc": "on"}),
            fake_stage(StageKey.CHAT_FETCH, journal, enabled=False),
        ),
        side_tasks=(
            fake_side_task(
                SideTaskKey.COVER, StageKey.METADATA, journal, enabled=False
            ),
        ),
        delivery=(fake_delivery("package", journal),),
    )
    assert pipeline.plan(options, loaded.config, archive=True) == (
        PlanEntry(
            "metadata",
            "Stage metadata",
            PlanKind.STAGE,
            True,
            {"official_cc": "on"},
            1,
        ),
        PlanEntry("chat_fetch", "Stage chat_fetch", PlanKind.STAGE, False, {}, 4),
        PlanEntry("package", "Deliver package", PlanKind.DELIVERY, True, {}, 3),
        PlanEntry("archive", "Archive", PlanKind.DELIVERY, True, {}, 1),
        PlanEntry("cover", "Side cover", PlanKind.SIDE_TASK, False, {}, 2),
    )


def test_break_after_plans_side_tasks_and_delivery_disabled(
    options: RunOptions, loaded: LoadedConfig
):
    journal = Journal()
    pipeline = Pipeline(
        (fake_stage(StageKey.METADATA, journal),),
        side_tasks=(fake_side_task(SideTaskKey.COVER, StageKey.METADATA, journal),),
        delivery=(fake_delivery("package", journal),),
    )
    entries = pipeline.plan(
        replace(options, break_after=StageKey.METADATA), loaded.config, archive=True
    )
    assert [(entry.key, entry.enabled) for entry in entries] == [
        ("metadata", True),
        ("package", False),
        ("archive", False),
        ("cover", False),
    ]


def test_archive_is_planned_only_when_wired(options: RunOptions, loaded: LoadedConfig):
    pipeline = Pipeline((fake_stage(StageKey.METADATA, Journal()),))
    assert [entry.key for entry in pipeline.plan(options, loaded.config)] == [
        "metadata"
    ]


def test_stage_lookup():
    stage = fake_stage(StageKey.ASR, Journal())
    pipeline = Pipeline((stage,))
    assert pipeline.stage(StageKey.ASR) is stage
    assert pipeline.stage(StageKey.AUDIO) is None


# Stages whose state writes deliberately survive a reset: ASR adds to the
# cumulative `asr_cost_usd`, and money paid stays paid.
KEEPS_STATE_ON_RESET = (StageKey.ASR,)
_STATE_WRITE = re.compile(r"ctx\.update\(|ctx\.state\.[\w.]+\s*=[^=]")


@pytest.mark.parametrize("stage", STAGES, ids=lambda stage: stage.key)
def test_a_stage_writing_state_clears_it_on_reset(stage: StageDef):
    module = inspect.getmodule(stage.run)
    assert module is not None
    writes_state = _STATE_WRITE.search(inspect.getsource(module)) is not None

    if stage.key in KEEPS_STATE_ON_RESET:
        assert writes_state, f"{stage.key} is exempt but writes no state"
    elif writes_state:
        assert stage.clear_state is not no_clear, (
            f"{stage.key} writes ProjectState but declares no clear_state"
        )
