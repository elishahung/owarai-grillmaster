from __future__ import annotations

import pytest

from grillmaster.core.model_spec import DEFAULT_EFFORT, Backend, Effort, ModelSpec


def test_backend_model_and_effort():
    assert ModelSpec.parse("codex/gpt-5.5/low") == ModelSpec(
        Backend.CODEX, "gpt-5.5", Effort.LOW
    )


@pytest.mark.parametrize("text", ["agy/gemini-3.1-pro", "agy/gemini-3.1-pro/"])
def test_missing_effort_defaults_to_high(text: str):
    spec = ModelSpec.parse(text)
    assert spec == ModelSpec(Backend.AGY, "gemini-3.1-pro", Effort.HIGH)
    assert DEFAULT_EFFORT is Effort.HIGH


def test_whitespace_is_trimmed():
    assert ModelSpec.parse("  codex / gpt-5.5 / medium  ") == ModelSpec(
        Backend.CODEX, "gpt-5.5", Effort.MEDIUM
    )


@pytest.mark.parametrize(
    ("text", "effort"),
    [
        ("codex/gpt-5.5/EXTRA", Effort.EXTRA),
        ("claude/claude-opus-4-8/Max", Effort.MAX),
        ("codex/gpt-5.6-sol/ULTRA", Effort.ULTRA),
    ],
)
def test_effort_is_case_insensitive(text: str, effort: Effort):
    assert ModelSpec.parse(text).effort is effort


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("codex/gpt-5.5/xhigh", "Unknown effort"),
        ("gpt-5.5", "backend/model"),
        ("codex/gpt-5.5/medium/high", "backend/model"),
        ("/gpt-5.5", "backend/model"),
        ("codex/", "backend/model"),
        ("gemini/gemini-3.1-pro", "Unknown backend"),
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


@pytest.mark.parametrize("model", ["", "a/b"])
def test_constructor_rejects_unusable_model_names(model: str):
    with pytest.raises(ValueError, match="Invalid model name"):
        ModelSpec(Backend.AGY, model, Effort.HIGH)
