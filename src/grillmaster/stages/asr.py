"""ASR stage: transcribe `work/05_audio/audio.ogg` with ElevenLabs into
`work/06_asr/asr.json` and add the spend to `ProjectState.asr_cost_usd`.

An existing `asr.json` is a cache hit: the stage costs nothing and records
nothing, so a resume after a failure past the request never pays twice. A
missing API key fails the run's preflight, before anything is downloaded.
The step result reports the stage's cost and the project's total.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.asr.client import AsrOptions, ensure_transcription
from grillmaster.core.stage_key import StageKey
from grillmaster.stages.base import StageDef, require

if TYPE_CHECKING:
    from grillmaster.config.model import AppConfig
    from grillmaster.config.secrets import Secrets
    from grillmaster.project.state import ProjectState
    from grillmaster.stages.base import StageContext


def _preflight(_config: AppConfig, secrets: Secrets) -> None:
    secrets.require_elevenlabs_api_key()


def _run(ctx: StageContext) -> str:
    layout = ctx.layout
    secrets = ctx.secrets
    cost = ensure_transcription(
        # The key itself was checked by the preflight.
        lambda: ctx.speech_to_text(secrets.require_elevenlabs_api_key()),
        AsrOptions(model=ctx.config.asr.model, language=ctx.config.asr.language),
        require(layout.audio, StageKey.AUDIO),
        layout.asr_json,
    )
    if cost is None:
        return f"cached · project ${ctx.state.asr_cost_usd:.4f}"

    def add_cost(state: ProjectState) -> None:
        state.add_asr_cost(cost.total_usd)

    ctx.update(add_cost)
    total = ctx.state.asr_cost_usd
    logger.info(
        f"Stage ASR cost: ${cost.total_usd:.4f} for {cost.audio_duration_s:.2f}s "
        f"(project ASR total ${total:.4f})"
    )
    return f"${cost.total_usd:.4f} · project ${total:.4f}"


def _params(config: AppConfig) -> dict[str, str]:
    return {"model": config.asr.model, "language": config.asr.language}


# No `clear_state`: `asr_cost_usd` is the project's cumulative spend, and money
# paid before a reset stays paid.
STAGE = StageDef(
    key=StageKey.ASR,
    label="Run ASR",
    weight=3,
    run=_run,
    params=_params,
    preflight=_preflight,
)
