from __future__ import annotations

from grillmaster.asr.srt_builder import SrtFormatOptions
from grillmaster.asr.srt_compensation import srt_options_for_model


def test_scribe_v2_turns_on_the_inner_silence_limit():
    assert srt_options_for_model("scribe_v2").inner_silence_limit_s > 0


def test_other_models_get_the_plain_defaults():
    assert srt_options_for_model("scribe_v1") == SrtFormatOptions()
