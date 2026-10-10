"""ASR stage: transcribe `work/05_audio/audio.ogg` with ElevenLabs into
`work/06_asr/asr.json` and add the spend to `ProjectState.asr_cost_usd`.

An existing `asr.json` is a cache hit: the stage costs nothing and records
nothing, so a resume after a failure past the request never pays twice. A
missing API key fails the run's preflight, before anything is downloaded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.asr.client import (
    AsrOptions,
    SpeechToTextFactory,
    connect_elevenlabs,
    ensure_transcription,
)
from grillmaster.asr.errors import AsrError
from grillmaster.config.secrets import ENV_FILE_NAME
from grillmaster.core.stage_key import StageKey
from grillmaster.pipeline.stage import StageDef, require

if TYPE_CHECKING:
    from grillmaster.config.model import AppConfig
    from grillmaster.config.secrets import Secrets
    from grillmaster.pipeline.stage import StageContext
    from grillmaster.project.state import ProjectState


def api_key(secrets: Secrets) -> str:
    """The ElevenLabs key; raises `AsrError` when it is not set."""
    key = secrets.elevenlabs_api_key
    if key is None or not key.get_secret_value():
        raise AsrError(
            f"ELEVENLABS_API_KEY is not set; add it to the {ENV_FILE_NAME} beside "
            "grill.toml or to the environment"
        )
    return key.get_secret_value()


def _preflight(_config: AppConfig, secrets: Secrets) -> None:
    api_key(secrets)


def build(connect: SpeechToTextFactory) -> StageDef:
    """The stage reaching ElevenLabs through `connect` (tests pass a fake)."""

    def run(ctx: StageContext) -> None:
        layout = ctx.layout
        cost = ensure_transcription(
            require(layout.audio, StageKey.AUDIO),
            layout.asr_json,
            options=AsrOptions(
                model=ctx.config.asr.model, language=ctx.config.asr.language
            ),
            api_key=api_key(ctx.secrets),
            connect=connect,
        )
        if cost is None:
            return

        def add_cost(state: ProjectState) -> None:
            state.add_asr_cost(cost.total_usd)

        ctx.update(add_cost)
        logger.info(
            f"Stage ASR cost: ${cost.total_usd:.4f} for "
            f"{cost.audio_duration_s:.2f}s (project ASR total "
            f"${ctx.state.asr_cost_usd:.4f})"
        )

    def params(config: AppConfig) -> dict[str, str]:
        return {"model": config.asr.model, "language": config.asr.language}

    # No `clear_state`: `asr_cost_usd` is the project's cumulative spend, and
    # money paid before a reset stays paid.
    return StageDef(
        key=StageKey.ASR,
        label="Run ASR",
        weight=3,
        run=run,
        outputs=lambda _layout: (),
        params=params,
        preflight=_preflight,
    )


STAGE = build(connect_elevenlabs)
