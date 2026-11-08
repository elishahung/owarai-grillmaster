from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import SecretStr

from grillmaster.asr.errors import AsrError
from grillmaster.asr.mai import USD_PER_AUDIO_HOUR, AzureSpeech
from grillmaster.asr.model import TranscriptionCost
from grillmaster.asr.timing import COLLAPSED_STACK_WORDS
from grillmaster.config.errors import ConfigError
from grillmaster.config.secrets import Secrets
from grillmaster.core.stage_key import StageKey
from grillmaster.core.talent import Talent
from grillmaster.project.store import load_state
from grillmaster.stages import asr

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from tests.stages.conftest import MakeContext

    from grillmaster.asr.mai import EpisodeTranscriber, MaiClientFactory
    from grillmaster.config.load import LoadedConfig
    from grillmaster.project.layout import ProjectLayout

AZURE_KEY = "az-test-key"
ENDPOINT = "https://speech.example"


def response(*words: tuple[str, int]) -> dict[str, Any]:
    """An hour-long MAI response, one phrase of `(text, offset_ms)` words."""
    return {
        "durationMilliseconds": 3_600_000,
        "phrases": [
            {
                "speaker": 0,
                "text": "".join(text for text, _ in words),
                "words": [
                    {"text": text, "offsetMilliseconds": at, "durationMilliseconds": 0}
                    for text, at in words
                ],
            }
        ],
    }


MAI_RESPONSE = response(("はい", 0))
COLLAPSED = response(*[("あ", 908_980)] * COLLAPSED_STACK_WORDS)


class FakeMai:
    """A MAI episode client writing `response`; also its factory. Records the
    credentials, the uploaded file (and whether it existed) and the phrases."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.credentials: list[AzureSpeech] = []
        self.calls: list[tuple[Path, bool, tuple[str, ...]]] = []

    def connect(self, credentials: AzureSpeech) -> EpisodeTranscriber:
        self.credentials.append(credentials)
        return self

    def transcribe(
        self, audio: Path, output: Path, phrases: Sequence[str]
    ) -> TranscriptionCost:
        self.calls.append((audio, audio.is_file(), tuple(phrases)))
        output.write_text(
            json.dumps(self.response, ensure_ascii=False), encoding="utf-8"
        )
        return TranscriptionCost.priced(
            self.response["durationMilliseconds"] / 1000, USD_PER_AUDIO_HOUR
        )


@pytest.fixture
def secrets(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Secrets:
    """The Azure Speech key and endpoint, unless parametrized indirectly
    with `None`."""
    for name in ("AZURE_SPEECH_KEY", "AZURE_SPEECH_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    if getattr(request, "param", "keys") is None:
        return Secrets()
    return Secrets(
        azure_speech_key=SecretStr(AZURE_KEY),
        azure_speech_endpoint=SecretStr(ENDPOINT),
    )


@pytest.fixture
def fake_mai() -> FakeMai:
    return FakeMai(MAI_RESPONSE)


@pytest.fixture
def mai(fake_mai: FakeMai) -> MaiClientFactory:
    return fake_mai.connect


@pytest.fixture
def audio_file(layout: ProjectLayout) -> None:
    layout.audio.parent.mkdir(parents=True)
    layout.audio.write_bytes(b"opus")


def keep(layout: ProjectLayout, payload: dict[str, Any]) -> None:
    layout.asr_json.parent.mkdir(parents=True)
    layout.asr_json.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.usefixtures("audio_file")
def test_mai_hears_a_flac_biased_toward_the_talents_and_records_the_cost(
    make_context: MakeContext,
    layout: ProjectLayout,
    fake_mai: FakeMai,
    capsys: pytest.CaptureFixture[str],
):
    ctx = make_context(StageKey.ASR)
    ctx.update(
        lambda state: setattr(
            state.source,
            "talents",
            [
                Talent(id="1", name="大悟"),
                Talent(id="2", name=" ノブ "),
                Talent(id="3", name="大悟"),
            ],
        )
    )
    ctx.state.add_asr_cost(0.5)

    result = asr.STAGE.run(ctx)

    assert fake_mai.credentials == [AzureSpeech(endpoint=ENDPOINT, key=AZURE_KEY)]
    ((upload, existed, phrases),) = fake_mai.calls
    assert upload == layout.asr_upload
    assert existed
    assert not upload.exists()
    assert phrases == ("大悟", "ノブ")
    assert json.loads(layout.asr_json.read_text(encoding="utf-8")) == MAI_RESPONSE
    expected = 0.5 + USD_PER_AUDIO_HOUR
    assert load_state(layout).asr_cost_usd == pytest.approx(expected)
    assert result == f"${USD_PER_AUDIO_HOUR:.4f} · project ${expected:.4f}"
    captured = capsys.readouterr()
    assert AZURE_KEY not in captured.out + captured.err


@pytest.mark.usefixtures("audio_file")
def test_a_failed_mai_request_still_deletes_the_upload(
    make_context: MakeContext, layout: ProjectLayout, fake_mai: FakeMai
):
    def fail(audio: Path, output: Path, phrases: Sequence[str]) -> TranscriptionCost:
        raise AsrError("HTTP 503")

    fake_mai.transcribe = fail  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(AsrError, match="HTTP 503"):
        asr.STAGE.run(make_context(StageKey.ASR))
    assert not layout.asr_upload.exists()
    assert not layout.asr_json.exists()


@pytest.mark.usefixtures("audio_file")
def test_an_existing_response_is_a_free_cache_hit(
    make_context: MakeContext, layout: ProjectLayout, fake_mai: FakeMai
):
    keep(layout, MAI_RESPONSE)
    ctx = make_context(StageKey.ASR)

    result = asr.STAGE.run(ctx)

    assert fake_mai.calls == []
    assert ctx.state.asr_cost_usd == 0.0
    assert result == "cached · project $0.0000"


@pytest.mark.usefixtures("audio_file")
def test_a_kept_scribe_response_fails_with_the_reset_to_run(
    make_context: MakeContext, layout: ProjectLayout, fake_mai: FakeMai
):
    scribe = {"words": [{"text": "はい", "start": 0.0, "end": 0.4, "type": "word"}]}
    keep(layout, scribe)

    with pytest.raises(AsrError, match=r"not a MAI-Transcribe-2 response.*--from asr"):
        asr.STAGE.run(make_context(StageKey.ASR))
    assert fake_mai.calls == []
    assert layout.asr_json.exists()


@pytest.mark.usefixtures("audio_file")
def test_collapsed_timings_are_paid_discarded_and_fail(
    make_context: MakeContext, layout: ProjectLayout
):
    ctx = make_context(StageKey.ASR, mai=FakeMai(COLLAPSED).connect)

    with pytest.raises(
        AsrError, match=r"MAI-Transcribe-2 stacked .*15:08: 100 words.*run again"
    ):
        asr.STAGE.run(ctx)
    assert not layout.asr_json.exists()
    assert load_state(layout).asr_cost_usd == pytest.approx(USD_PER_AUDIO_HOUR)


@pytest.mark.usefixtures("audio_file")
def test_a_cached_collapsed_response_is_discarded_too(
    make_context: MakeContext, layout: ProjectLayout, fake_mai: FakeMai
):
    keep(layout, COLLAPSED)

    with pytest.raises(AsrError, match="15:08: 100 words"):
        asr.STAGE.run(make_context(StageKey.ASR))
    assert fake_mai.calls == []
    assert not layout.asr_json.exists()


@pytest.mark.parametrize("secrets", [None], indirect=True)
def test_preflight_rejects_a_missing_azure_key(loaded: LoadedConfig, secrets: Secrets):
    with pytest.raises(ConfigError, match=r"AZURE_SPEECH_ENDPOINT is not set.*\.env"):
        asr.STAGE.preflight(loaded.config, secrets)


def test_preflight_accepts_the_azure_keys(loaded: LoadedConfig, secrets: Secrets):
    asr.STAGE.preflight(loaded.config, secrets)
