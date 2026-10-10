from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from pydantic import SecretStr
from tests.fakes import FakeSpeechToText

from grillmaster.asr.client import PRICE_PER_HOUR_USD
from grillmaster.config.errors import ConfigError
from grillmaster.config.secrets import Secrets
from grillmaster.core.stage_key import StageKey
from grillmaster.project.store import load_state
from grillmaster.stages import asr

if TYPE_CHECKING:
    from tests.stages.conftest import MakeContext

    from grillmaster.asr.client import SpeechToTextFactory
    from grillmaster.config.load import LoadedConfig
    from grillmaster.project.layout import ProjectLayout

API_KEY = "el-test-key"
RESPONSE = {
    "text": "はい。",
    "words": [{"text": "はい。", "start": 0.0, "end": 0.4, "type": "word"}],
    "audio_duration_secs": 3600,
}


@pytest.fixture
def secrets(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Secrets:
    """`API_KEY`, unless parametrized indirectly with another key or `None`."""
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    key: str | None = getattr(request, "param", API_KEY)
    return Secrets(elevenlabs_api_key=None if key is None else SecretStr(key))


@pytest.fixture
def api() -> FakeSpeechToText:
    return FakeSpeechToText(RESPONSE)


@pytest.fixture
def speech_to_text(api: FakeSpeechToText) -> SpeechToTextFactory:
    return api.connect


@pytest.fixture
def audio_file(layout: ProjectLayout) -> None:
    layout.audio.parent.mkdir(parents=True)
    layout.audio.write_bytes(b"opus")


@pytest.mark.usefixtures("audio_file")
def test_transcribes_and_records_the_cost(
    make_context: MakeContext,
    layout: ProjectLayout,
    api: FakeSpeechToText,
    capsys: pytest.CaptureFixture[str],
):
    ctx = make_context(StageKey.ASR)
    ctx.state.add_asr_cost(0.5)

    result = asr.STAGE.run(ctx)

    assert api.api_keys == [API_KEY]
    (call,) = api.calls
    assert call["model_id"] == "scribe_v2"
    assert call["language_code"] == "jpn"
    assert call["file"] == b"opus"
    assert json.loads(layout.asr_json.read_text(encoding="utf-8")) == RESPONSE
    expected = 0.5 + PRICE_PER_HOUR_USD
    assert ctx.state.asr_cost_usd == pytest.approx(expected)
    assert load_state(layout).asr_cost_usd == pytest.approx(expected)
    assert result == f"${PRICE_PER_HOUR_USD:.4f} · project ${expected:.4f}"
    captured = capsys.readouterr()
    assert API_KEY not in captured.out + captured.err


@pytest.mark.usefixtures("audio_file")
def test_existing_response_is_a_free_cache_hit(
    make_context: MakeContext, layout: ProjectLayout, api: FakeSpeechToText
):
    layout.asr_json.parent.mkdir(parents=True)
    layout.asr_json.write_text("{}", encoding="utf-8")
    ctx = make_context(StageKey.ASR)

    result = asr.STAGE.run(ctx)

    assert api.api_keys == []
    assert api.calls == []
    assert layout.asr_json.read_text(encoding="utf-8") == "{}"
    assert ctx.state.asr_cost_usd == 0.0
    assert result == "cached · project $0.0000"


@pytest.mark.parametrize("secrets", [None, ""], indirect=True)
@pytest.mark.usefixtures("audio_file")
def test_missing_api_key_fails_before_any_request(
    make_context: MakeContext, layout: ProjectLayout, api: FakeSpeechToText
):
    with pytest.raises(ConfigError, match=r"ELEVENLABS_API_KEY is not set.*\.env"):
        asr.STAGE.run(make_context(StageKey.ASR))
    assert api.api_keys == []
    assert not layout.asr_json.exists()


@pytest.mark.parametrize("secrets", [None], indirect=True)
def test_preflight_rejects_a_missing_key(loaded: LoadedConfig, secrets: Secrets):
    with pytest.raises(ConfigError, match="ELEVENLABS_API_KEY is not set"):
        asr.STAGE.preflight(loaded.config, secrets)


def test_preflight_accepts_a_key(loaded: LoadedConfig, secrets: Secrets):
    asr.STAGE.preflight(loaded.config, secrets)
