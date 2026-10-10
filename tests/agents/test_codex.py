from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from tests.agents.fakes import FakeProcess, FakeSpawn, fixture_lines

from grillmaster.agents.adapters.base import McpServer
from grillmaster.agents.adapters.codex import CodexAdapter
from grillmaster.core.model_spec import Backend, Effort, ModelSpec

if TYPE_CHECKING:
    from collections.abc import Callable

    from grillmaster.agents.adapters.base import TurnRequest

SPEC = ModelSpec(Backend.CODEX, "gpt-6-astra", Effort.EXTRA)
MCP = McpServer(
    r"C:\venv\python.exe",
    ("-m", "grillmaster.agent_tools", "--session", r"C:\s\tools.json"),
)


def _value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def _configs(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, arg in enumerate(argv) if arg == "-c"]


def test_start_argv(make_request: Callable[..., TurnRequest], tmp_path: Path):
    image = tmp_path / "poster.jpg"
    spawn = FakeSpawn(FakeProcess(fixture_lines("codex", "mcp_schema")))
    request = make_request(
        spec=SPEC,
        schema={"type": "object"},
        images=(image,),
        mcp=MCP,
        message="do it",
        web_search=True,
    )
    CodexAdapter(spawn=spawn, executable="codex").start(request).result()

    spec = spawn.specs[0]
    argv = list(spec.argv)
    assert argv[:4] == ["codex", "exec", "--image", str(image)]
    # A flag right after the images ends `--image`'s value list.
    assert argv[4] == "--json"
    assert _value(argv, "-m") == "gpt-6-astra"
    assert _value(argv, "--cd") == str(request.workdir)
    for flag in (
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--dangerously-bypass-approvals-and-sandbox",
    ):
        assert flag in argv
    assert "--ephemeral" not in argv
    assert _configs(argv) == [
        "model_reasoning_effort=xhigh",
        "tools.web_search=true",
        'mcp_servers.grill.command="C:\\\\venv\\\\python.exe"',
        (
            'mcp_servers.grill.args=["-m", "grillmaster.agent_tools", "--session", '
            '"C:\\\\s\\\\tools.json"]'
        ),
    ]
    schema_file = Path(_value(argv, "--output-schema"))
    assert json.loads(schema_file.read_text(encoding="utf-8")) == {"type": "object"}
    assert argv[-1] == "-"
    assert spec.stdin == "do it"
    assert not spec.keep_stdin_open
    assert spec.cwd == request.workdir


def test_resume_runs_in_the_workdir_without_cd(
    make_request: Callable[..., TurnRequest], tmp_path: Path
):
    spawn = FakeSpawn(
        FakeProcess(fixture_lines("codex", "mcp_schema")),
        FakeProcess(fixture_lines("codex", "resume")),
    )
    adapter = CodexAdapter(spawn=spawn, executable="codex")
    request = make_request(
        spec=SPEC, schema={"type": "object"}, mcp=MCP, images=(tmp_path / "a.png",)
    )
    adapter.start(request).result()
    adapter.resume("thread-1", request).result()

    start, resumed = (list(spec.argv) for spec in spawn.specs)
    assert resumed[:4] == ["codex", "exec", "resume", "thread-1"]
    assert "--cd" not in resumed
    assert "--image" not in resumed
    assert _configs(resumed) == _configs(start)
    assert _value(resumed, "--output-schema") == _value(start, "--output-schema")
    assert _value(resumed, "-m") == "gpt-6-astra"
    assert spawn.specs[1].cwd == request.workdir


@pytest.mark.parametrize(
    ("effort", "value"),
    [(Effort.LOW, "low"), (Effort.EXTRA, "xhigh"), (Effort.ULTRA, "ultra")],
)
def test_effort_mapping(
    effort: Effort, value: str, make_request: Callable[..., TurnRequest]
):
    adapter = CodexAdapter(spawn=FakeSpawn(), executable="codex")
    argv = adapter.argv(
        make_request(spec=ModelSpec(Backend.CODEX, "m", effort)), thread=None
    )
    assert f"model_reasoning_effort={value}" in _configs(argv)


def test_web_search_is_enabled_only_when_required(
    make_request: Callable[..., TurnRequest],
):
    adapter = CodexAdapter(spawn=FakeSpawn(), executable="codex")
    argv = adapter.argv(make_request(spec=SPEC), thread=None)
    assert _configs(argv) == ["model_reasoning_effort=xhigh"]


def test_no_schema_means_no_structured_output(
    make_request: Callable[..., TurnRequest],
):
    spawn = FakeSpawn(FakeProcess(fixture_lines("codex", "resume")))
    request = make_request(spec=SPEC)
    final = CodexAdapter(spawn=spawn, executable="codex").start(request).result()
    assert "--output-schema" not in spawn.specs[0].argv
    assert final.structured is None
    assert final.text.startswith('{"answer"')


def test_schema_turn_with_prose_yields_no_structured_output(
    make_request: Callable[..., TurnRequest],
):
    lines = [
        '{"type":"thread.started","thread_id":"t"}',
        '{"type":"item.completed","item":{"id":"i","type":"agent_message","text":"sorry"}}',
        '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}',
    ]
    spawn = FakeSpawn(FakeProcess(lines))
    request = make_request(spec=SPEC, schema={"type": "object"})
    final = CodexAdapter(spawn=spawn, executable="codex").start(request).result()
    assert final.structured is None
    assert final.text == "sorry"
