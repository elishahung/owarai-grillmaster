"""ASR stage: transcribe `.work/05_audio/audio.ogg` with `[asr] provider`
into `.work/06_asr/asr.json` (the provider's raw response) and add the
spend to `ProjectState.asr_cost_usd`.

MAI-Transcribe-2 takes no Opus, so it gets a FLAC made from `audio.ogg`
beside the response (`layout.asr_upload`) and deleted once the request
ends, paid or not; one a killed run left behind is dropped by the archive
move. Its recognition is biased toward the
source's credited talent names. Scribe reads `audio.ogg` as it is.

An existing `asr.json` is a cache hit, whichever provider wrote it: the
stage costs nothing and records nothing, so a resume after a failure past
the request never pays twice, and changing `[asr] provider` reaches a
project only after `grill reset <id> --from asr`. A missing key fails the
run's preflight, before anything is downloaded. The step result reports the
stage's cost and the project's total.

A response with collapsed word timings (`asr/timing.py`), cached or new,
fails the stage after its cost is recorded and is deleted, so the next run
requests a new one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.asr import scribe
from grillmaster.asr.errors import AsrError
from grillmaster.asr.mai import AzureSpeech
from grillmaster.asr.payload import read_payload
from grillmaster.asr.providers import for_response
from grillmaster.asr.timing import collapsed_stacks
from grillmaster.core.asr_provider import AsrProvider
from grillmaster.core.stage_key import StageKey
from grillmaster.media.audio import extract_audio
from grillmaster.stages.base import StageDef, require

if TYPE_CHECKING:
    from grillmaster.asr.model import TranscriptionCost
    from grillmaster.config.model import AppConfig
    from grillmaster.config.secrets import Secrets
    from grillmaster.project.state import ProjectState
    from grillmaster.stages.base import StageContext


def _preflight(config: AppConfig, secrets: Secrets) -> None:
    secrets.require_speech_keys(config.asr.provider)


def _run(ctx: StageContext) -> str:
    layout = ctx.layout
    cost: TranscriptionCost | None = None
    if layout.asr_json.exists():
        logger.info(f"Reusing ASR response: {layout.asr_json}")
    else:
        cost = _transcribe(ctx)
    if cost is not None:

        def add_cost(state: ProjectState) -> None:
            state.add_asr_cost(cost.total_usd)

        ctx.update(add_cost)
        logger.info(
            f"Stage ASR cost: ${cost.total_usd:.4f} for {cost.audio_duration_s:.2f}s "
            f"(project ASR total ${ctx.state.asr_cost_usd:.4f})"
        )
    # Checked once the cost is recorded: a discarded response was still paid.
    payload = read_payload(layout.asr_json)
    provider = for_response(payload)
    if stacks := collapsed_stacks(provider.timed_words(payload)):
        layout.asr_json.unlink()
        raise AsrError(
            f"{provider.label} stacked words on one timestamp "
            f"({'; '.join(stacks)}); the response was discarded, run again to "
            "request a new transcription"
        )
    total = ctx.state.asr_cost_usd
    if cost is None:
        return f"cached · project ${total:.4f}"
    return f"${cost.total_usd:.4f} · project ${total:.4f}"


def _transcribe(ctx: StageContext) -> TranscriptionCost:
    layout = ctx.layout
    audio = require(layout.audio, StageKey.AUDIO)
    # The keys were checked by the preflight.
    match ctx.config.asr.provider:
        case AsrProvider.SCRIBE:
            api = ctx.speech_to_text(ctx.secrets.require_elevenlabs_api_key())
            return scribe.transcribe(api, audio, layout.asr_json)
        case AsrProvider.MAI:
            endpoint, key = ctx.secrets.require_azure_speech()
            upload = layout.asr_upload
            try:
                extract_audio(ctx.ffmpeg, audio, upload)
                return ctx.mai(AzureSpeech(endpoint=endpoint, key=key)).transcribe(
                    upload, layout.asr_json, talent_names(ctx.state)
                )
            finally:
                upload.unlink(missing_ok=True)


def talent_names(state: ProjectState) -> tuple[str, ...]:
    """The source's credited talents, once each: the episode's speakers
    before the pre-pass has named its cast."""
    names = (talent.name.strip() for talent in state.source.talents)
    return tuple(dict.fromkeys(name for name in names if name))


def _params(config: AppConfig) -> dict[str, str]:
    return {"provider": config.asr.provider}


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
