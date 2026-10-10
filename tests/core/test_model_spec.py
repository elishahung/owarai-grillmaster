from __future__ import annotations

import pytest

from grillmaster.core.model_spec import DEFAULT_EFFORT, Backend, Effort, ModelSpec, Role


def test_backend_model_and_effort():
    assert ModelSpec.parse("codex/gpt-5.5/low") == ModelSpec(
        Backend.CODEX, "gpt-5.5", Effort.LOW
    )


def test_missing_effort_defaults_to_high():
    spec = ModelSpec.parse("agy/gemini-3.1-pro")
    assert spec == ModelSpec(Backend.AGY, "gemini-3.1-pro", Effort.HIGH)
    assert DEFAULT_EFFORT is Effort.HIGH


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("codex/gpt-5.5/xhigh", "Unknown effort"),
        ("codex/gpt-5.5/HIGH", "Unknown effort"),
        ("codex/gpt-5.5/", "backend/model"),
        (" codex/gpt-5.5", "Unknown backend"),
        ("codex/gpt-5.5/ medium", "Unknown effort"),
        ("codex/ gpt-5.5", "Invalid model name"),
        ("gpt-5.5", "backend/model"),
        ("codex/gpt-5.5/medium/high", "backend/model"),
        ("/gpt-5.5", "backend/model"),
        ("codex/", "backend/model"),
        ("openai/gpt-x", "Unknown backend"),
    ],
)
def test_invalid_specs_raise(text: str, match: str):
    with pytest.raises(ValueError, match=match):
        ModelSpec.parse(text)


def test_str_round_trips():
    spec = ModelSpec(Backend.CODEX, "m", Effort.LOW)
    assert str(spec) == "codex/m/low"
    assert ModelSpec.parse(str(spec)) == spec


def test_str_always_prints_the_effort():
    assert (
        str(ModelSpec.parse("claude/claude-opus-4-8")) == "claude/claude-opus-4-8/high"
    )


@pytest.mark.parametrize("model", ["", "a/b", " m", "a b"])
def test_constructor_rejects_unusable_model_names(model: str):
    with pytest.raises(ValueError, match="Invalid model name"):
        ModelSpec(Backend.AGY, model, Effort.HIGH)


def test_role_values_match_grill_toml_keys():
    assert [str(role) for role in Role] == [
        "prepass",
        "chunk",
        "postprocess",
        "utility",
        "chat",
        "image",
    ]
