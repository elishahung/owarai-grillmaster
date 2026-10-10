from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from grillmaster.config.model import (
    INSERT_OUTPUT_MAX_LENGTH,
    INSTRUCTION_STAGES,
    AgentRoles,
    AppConfig,
    ProgramInstruction,
    validate_config,
)
from grillmaster.core.model_spec import Backend, Effort, ModelSpec, Role
from grillmaster.core.stage_key import StageKey


def test_minimal_config_fills_in_defaults(minimal_data: dict[str, Any]):
    config = validate_config(minimal_data, root=Path("/root"))

    assert config.paths.archive is None
    assert config.agents.max_concurrent == 5
    assert config.translate.chunk_char_limit == 6000
    assert config.features.official_subtitles
    assert not config.features.cover
    assert config.package.inserts == []
    assert config.programs.series == {}


def test_role_strings_parse_into_model_specs(minimal_data: dict[str, Any]):
    roles = validate_config(minimal_data, root=Path()).agents.roles
    assert roles.prepass == ModelSpec(Backend.AGY, "gemini-3.1-pro", Effort.HIGH)
    assert roles.postprocess == ModelSpec(Backend.CODEX, "gpt-5.6-sol", Effort.MEDIUM)


def test_chat_falls_back_to_utility(roles: dict[str, str]):
    agent_roles = AgentRoles.model_validate(roles)
    assert agent_roles.spec(Role.CHAT) == agent_roles.utility


def test_explicit_chat_spec_wins(roles: dict[str, str]):
    agent_roles = AgentRoles.model_validate({**roles, "chat": "claude/opus/max"})
    assert agent_roles.spec(Role.CHAT) == ModelSpec(Backend.CLAUDE, "opus", Effort.MAX)


def test_specs_cover_every_role(roles: dict[str, str]):
    assert set(AgentRoles.model_validate(roles).specs()) == set(Role)
    assert set(AgentRoles.model_fields) == {str(role) for role in Role}


@pytest.mark.parametrize(
    "role", ["prepass", "chunk", "postprocess", "utility", "image"]
)
def test_every_role_but_chat_is_required(roles: dict[str, str], role: str):
    del roles[role]
    with pytest.raises(ValidationError, match=role):
        AgentRoles.model_validate(roles)


@pytest.mark.parametrize(
    ("spec", "match"),
    [
        ("gemini/x", "Unknown backend"),
        ("agy/x/xhigh", "Unknown effort"),
        ("agy/x/HIGH", "Unknown effort"),
        (3, "string"),
    ],
)
def test_bad_model_specs_fail(roles: dict[str, str], spec: object, match: str):
    with pytest.raises(ValidationError, match=match):
        AgentRoles.model_validate({**roles, "chunk": spec})


@pytest.mark.parametrize(
    "spec",
    [
        "agy/m",
        "codex/gpt-5.5/low",
        "claude/opus/ultra",
        "agy/m/",
        "agy/m/HIGH",
        " agy/m",
        "agy/a b",
        "agy/m/high/x",
        "gemini/m",
    ],
)
def test_schema_pattern_accepts_exactly_what_parses(spec: str):
    pattern = AgentRoles.model_json_schema()["properties"]["chunk"]["pattern"]
    try:
        ModelSpec.parse(spec)
    except ValueError:
        parses = False
    else:
        parses = True
    assert (re.fullmatch(pattern, spec) is not None) == parses


def test_model_specs_dump_as_strings(minimal_data: dict[str, Any]):
    dumped = validate_config(minimal_data, root=Path()).model_dump(mode="json")
    assert dumped["agents"]["roles"]["chunk"] == "agy/gemini-3.1-pro/high"
    assert dumped["agents"]["roles"]["chat"] is None


@pytest.mark.parametrize(
    "patch",
    [
        {"typo": 1},
        {"translate": {"chunk_char_limt": 1}},
        {"agents": {"timeout": 1}},
        {"programs": {"series": {"X": {"remx": True}}}},
        # Legacy step names are not aliases.
        {"programs": {"series": {"X": {"instruction": {"pre_pass": "t"}}}}},
        {"programs": {"films": {}}},
    ],
)
def test_unknown_keys_fail_loudly(minimal_data: dict[str, Any], patch: dict[str, Any]):
    data = _merge(minimal_data, patch)
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        validate_config(data, root=Path())


def test_agents_section_is_required():
    with pytest.raises(ValidationError, match="agents"):
        validate_config({}, root=Path())


def test_relative_paths_resolve_against_root(
    minimal_data: dict[str, Any], tmp_path: Path
):
    absolute = tmp_path / "archive"
    data = {**minimal_data, "paths": {"archive": str(absolute), "cookies": "c.txt"}}
    config = validate_config(data, root=tmp_path / "home")
    assert config.paths.archive == absolute
    assert config.paths.cookies == tmp_path / "home" / "c.txt"


def test_home_relative_paths_expand(minimal_data: dict[str, Any], tmp_path: Path):
    data = {**minimal_data, "paths": {"cookies": "~/c.txt"}}
    config = validate_config(data, root=tmp_path)
    assert config.paths.cookies == Path.home() / "c.txt"


def test_relative_path_without_root_fails(minimal_data: dict[str, Any]):
    data = {**minimal_data, "paths": {"cookies": "c.txt"}}
    with pytest.raises(ValidationError, match=r"grill\.toml directory"):
        AppConfig.model_validate(data)


def test_insert_outputs_must_be_unique(minimal_data: dict[str, Any]):
    inserts = [{"pool": "a", "output": "judge"}, {"pool": "b", "output": "judge"}]
    data = {**minimal_data, "package": {"inserts": inserts}}
    with pytest.raises(ValidationError, match="duplicate insert outputs: judge"):
        validate_config(data, root=Path())


def test_insert_when_defaults_to_remix(minimal_data: dict[str, Any]):
    data = {**minimal_data, "package": {"inserts": [{"pool": "p", "output": "o"}]}}
    assert validate_config(data, root=Path()).package.inserts[0].when == "remix"


@pytest.mark.parametrize("name", ["", "a/b", "a\\b", "c:"])
def test_pool_and_output_names_are_plain_file_names(
    minimal_data: dict[str, Any], name: str
):
    data = {**minimal_data, "package": {"inserts": [{"pool": name, "output": "o"}]}}
    with pytest.raises(ValidationError):
        validate_config(data, root=Path())


@pytest.mark.parametrize(
    ("output", "message"),
    [
        ("video", "reserved"),
        ("Cover", "reserved"),
        ("info", "reserved"),
        ("refine", "reserved"),
        ("glossary_check", "reserved"),
        ("12", "all digits"),
        ("x" * (INSERT_OUTPUT_MAX_LENGTH + 1), "at most"),
    ],
)
def test_insert_outputs_avoid_the_deliverables_own_names(
    minimal_data: dict[str, Any], output: str, message: str
):
    data = {**minimal_data, "package": {"inserts": [{"pool": "p", "output": output}]}}
    with pytest.raises(ValidationError, match=message):
        validate_config(data, root=Path())


def test_an_insert_output_may_use_the_whole_length(minimal_data: dict[str, Any]):
    output = "x" * INSERT_OUTPUT_MAX_LENGTH
    data = {**minimal_data, "package": {"inserts": [{"pool": "p", "output": output}]}}
    assert validate_config(data, root=Path()).package.inserts[0].output == output


def test_programs_may_only_list_declared_inserts(minimal_data: dict[str, Any]):
    data = {
        **minimal_data,
        "package": {"inserts": [{"pool": "judge", "output": "judge"}]},
        "programs": {"channel": {"日テレ": {"inserts": ["judge", "bell"]}}},
    }
    with pytest.raises(
        ValidationError, match="channel '日テレ' lists undeclared inserts: bell"
    ):
        validate_config(data, root=Path())


def test_instruction_fields_are_common_plus_instruction_stages():
    assert list(ProgramInstruction.model_fields) == [
        "common",
        *(str(stage) for stage in INSTRUCTION_STAGES),
    ]


def test_stage_text_by_stage_key():
    instruction = ProgramInstruction(common="c", chunks="t", glossary="g")
    assert instruction.stage_text(StageKey.CHUNKS) == "t"
    assert instruction.stage_text(StageKey.GLOSSARY) == "g"
    assert instruction.stage_text(StageKey.PREPASS) is None


def test_stage_text_rejects_stages_without_instructions():
    with pytest.raises(ValueError, match="takes no program instructions"):
        ProgramInstruction().stage_text(StageKey.FINALIZE)


def _merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged
