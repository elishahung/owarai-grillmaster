from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from tests.agents.fakes import FakeProcess, FakeSpawn, fixture_lines

from grillmaster.agents.adapters._google import API_KEY_ENV_VARS
from grillmaster.agents.adapters.agy import (
    PROBE_FAILURE_TTL_S,
    AgyAdapter,
    AgyTurnParser,
    parse_model_list,
)
from grillmaster.agents.adapters.base import McpServer
from grillmaster.agents.errors import AgentConfigError
from grillmaster.agents.events import Message
from grillmaster.core.model_spec import Backend, Effort, ModelSpec

if TYPE_CHECKING:
    from collections.abc import Callable

    from grillmaster.agents.adapters.base import TurnRequest

MODELS = ["gemini-3.1-pro-high", "gemini-3.1-pro-low", "gemini-3.8-flash-medium"]
PRO_HIGH = ModelSpec(Backend.AGY, "gemini-3.1-pro", Effort.HIGH)


def _adapter(spawn: FakeSpawn, models: list[str] | None = None) -> AgyAdapter:
    return AgyAdapter(
        spawn=spawn, executable="agy", list_models=lambda: models or MODELS
    )


def _run(adapter: AgyAdapter, request: TurnRequest, *, resume: str | None = None):
    handle = (
        adapter.start(request) if resume is None else adapter.resume(resume, request)
    )
    return handle.result()


def test_start_argv_puts_every_flag_before_the_empty_print_flag(
    make_request: Callable[..., TurnRequest], tmp_path: Path
):
    image = tmp_path / "frames" / "a.jpg"
    audio = tmp_path / "chunk" / "audio.ogg"
    extra = tmp_path / "project"
    spawn = FakeSpawn(FakeProcess(fixture_lines("agy", "text_simple")))
    request = make_request(
        spec=PRO_HIGH,
        schema={"type": "object"},
        images=(image,),
        audio=(audio,),
        add_dirs=(extra,),
    )
    _run(_adapter(spawn), request)

    spec = spawn.specs[0]
    argv = list(spec.argv)
    assert argv[0] == "agy"
    assert argv[argv.index("--model") + 1] == "gemini-3.1-pro-high"
    assert "--dangerously-skip-permissions" in argv
    assert argv[argv.index("--log-file") + 1] == str(request.session_dir / "agy.log")
    schema_file = Path(argv[argv.index("--json-schema") + 1])
    assert json.loads(schema_file.read_text(encoding="utf-8")) == {"type": "object"}
    added = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--add-dir"]
    assert added == [
        str(request.workdir),
        str(extra),
        str(image.parent),
        str(audio.parent),
    ]
    assert "--conversation" not in argv
    assert argv[-5:] == [
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "-p=",
    ]
    assert spec.cwd == request.workdir
    assert spec.keep_stdin_open
    assert json.loads(spec.stdin) == {"event": "user", "message": {"content": "hello"}}


def test_resume_repeats_the_flags_and_names_the_conversation(
    make_request: Callable[..., TurnRequest],
):
    spawn = FakeSpawn(
        FakeProcess(fixture_lines("agy", "text_simple")),
        FakeProcess(fixture_lines("agy", "resume")),
    )
    adapter = _adapter(spawn)
    request = make_request(spec=PRO_HIGH, schema={"type": "object"})
    _run(adapter, request)
    _run(
        adapter,
        make_request(spec=PRO_HIGH, schema={"type": "object"}, message="fix"),
        resume="conv-1",
    )

    start, resumed = (list(spec.argv) for spec in spawn.specs)
    assert resumed[resumed.index("--conversation") + 1] == "conv-1"
    resumed.remove("--conversation")
    resumed.remove("conv-1")
    assert resumed == start
    assert json.loads(spawn.specs[1].stdin)["message"]["content"] == "fix"


@pytest.mark.parametrize(
    ("effort", "model_id"),
    [
        (Effort.LOW, "gemini-3.1-pro-low"),
        (Effort.HIGH, "gemini-3.1-pro-high"),
        (Effort.EXTRA, "gemini-3.1-pro-high"),
        (Effort.ULTRA, "gemini-3.1-pro-high"),
    ],
)
def test_effort_is_part_of_the_model_id(effort: Effort, model_id: str):
    spec = ModelSpec(Backend.AGY, "gemini-3.1-pro", effort)
    assert _adapter(FakeSpawn()).model_id(spec) == model_id


def test_unknown_model_fails_preflight_listing_the_available_ids():
    spec = ModelSpec(Backend.AGY, "gemini-3.1-pro", Effort.MEDIUM)
    with pytest.raises(AgentConfigError, match=r"gemini-3\.1-pro-medium") as caught:
        _adapter(FakeSpawn()).preflight(spec, (), ())
    assert "gemini-3.1-pro-high, gemini-3.1-pro-low" in str(caught.value)


def test_model_list_is_fetched_once():
    calls: list[int] = []

    def models() -> list[str]:
        calls.append(1)
        return MODELS

    adapter = AgyAdapter(spawn=FakeSpawn(), executable="agy", list_models=models)
    adapter.preflight(PRO_HIGH, (), ())
    adapter.model_id(PRO_HIGH)
    assert calls == [1]


def test_model_list_comes_from_agy_models():
    output = [
        "Fetching available models...",
        "gemini-3.1-pro-high\tGemini 3.1 Pro (High)",
        "gemini-3.1-pro-low\tGemini 3.1 Pro (Low)",
    ]
    spawn = FakeSpawn(FakeProcess(output))
    adapter = AgyAdapter(spawn=spawn, executable="agy")
    assert adapter.model_id(PRO_HIGH) == "gemini-3.1-pro-high"
    assert list(spawn.specs[0].argv) == ["agy", "models"]


def test_a_failed_model_probe_answers_later_tasks_until_it_expires():
    now = [0.0]
    spawn = FakeSpawn(
        FakeProcess([], returncode=1, stderr="not signed in"),
        FakeProcess([], returncode=1, stderr="not signed in"),
    )
    adapter = AgyAdapter(spawn=spawn, executable="agy", clock=lambda: now[0])
    for at in (0.0, PROBE_FAILURE_TTL_S - 1, PROBE_FAILURE_TTL_S + 1):
        now[0] = at
        with pytest.raises(AgentConfigError, match="not signed in"):
            adapter.preflight(PRO_HIGH, (), ())
    # The second task shared the first failure; the third probed again.
    assert len(spawn.specs) == 2


def test_a_timed_out_model_probe_does_not_fail_tasks_after_it_expires():
    now = [0.0]
    listed = ["gemini-3.1-pro-high\tGemini 3.1 Pro (High)"]
    spawn = FakeSpawn(FakeProcess([], timed_out=True), FakeProcess(listed))
    adapter = AgyAdapter(spawn=spawn, executable="agy", clock=lambda: now[0])
    with pytest.raises(AgentConfigError, match="timed out"):
        adapter.preflight(PRO_HIGH, (), ())

    now[0] = PROBE_FAILURE_TTL_S
    adapter.preflight(PRO_HIGH, (), ())
    now[0] = 10 * PROBE_FAILURE_TTL_S
    adapter.preflight(PRO_HIGH, (), ())  # the success is remembered
    assert len(spawn.specs) == 2


def test_parse_model_list_skips_banner_lines():
    text = "Fetching...\n\ngpt-oss-120b-medium\tGPT-OSS 120B (Medium)\n"
    assert parse_model_list(text) == ["gpt-oss-120b-medium"]


def test_paid_api_keys_are_scrubbed(
    make_request: Callable[..., TurnRequest], monkeypatch: pytest.MonkeyPatch
):
    for key in API_KEY_ENV_VARS:
        monkeypatch.setenv(key, "paid")
    monkeypatch.setenv("ANTIGRAVITY_API_KEY", "own")
    spawn = FakeSpawn(FakeProcess(fixture_lines("agy", "text_simple")))
    _run(_adapter(spawn), make_request(spec=PRO_HIGH))

    env = spawn.specs[0].env
    assert env is not None
    assert not set(API_KEY_ENV_VARS) & set(env)
    assert env["ANTIGRAVITY_API_KEY"] == "own"


def test_mcp_server_goes_into_the_workspace_config(
    make_request: Callable[..., TurnRequest],
):
    spawn = FakeSpawn(
        FakeProcess(fixture_lines("agy", "text_simple")),
        FakeProcess(fixture_lines("agy", "text_simple")),
    )
    adapter = _adapter(spawn)
    mcp = McpServer("python", ("-m", "grillmaster.agent_tools", "--session", "t.json"))
    request = make_request(spec=PRO_HIGH, mcp=mcp)
    _run(adapter, request)

    config_path = request.workdir / ".agents" / "mcp_config.json"
    assert json.loads(config_path.read_text(encoding="utf-8")) == {
        "mcpServers": {
            "grill": {
                "command": "python",
                "args": ["-m", "grillmaster.agent_tools", "--session", "t.json"],
            }
        }
    }
    _run(adapter, make_request(spec=PRO_HIGH))
    assert not config_path.exists()


def _turn_without_finish(answer: str, *, schema: bool) -> object:
    """A finished turn without a `finish` step whose result still carries
    the previous turn's structured value."""
    parser = AgyTurnParser((), schema=schema)
    records = [
        {"event": "init", "conversation_id": "c1"},
        {
            "event": "step_update",
            "step_update": {
                "step_index": 1,
                "state": "DONE",
                "step_type": "agent_response",
                "text_delta": answer,
            },
        },
        {
            "event": "result",
            "result": {
                "status": "SUCCESS",
                "response": answer,
                "structured_output": {"color": "stale"},
            },
        },
    ]
    for record in records:
        list(parser.feed(record))
    return parser.finish(0, "").structured


def test_without_finish_the_turns_own_json_answer_is_the_structured_output():
    answer = '```json\n{"color": "fresh"}\n```'
    assert _turn_without_finish(answer, schema=True) == {"color": "fresh"}


def test_without_finish_a_prose_answer_is_no_structured_output():
    assert _turn_without_finish("The color is fresh.", schema=True) is None


def test_without_a_schema_no_structured_output_is_read_from_text():
    assert _turn_without_finish('{"color": "fresh"}', schema=False) is None


def test_closing_the_events_early_stops_the_cli(
    make_request: Callable[..., TurnRequest],
):
    lines = [
        json.dumps({"event": "init", "conversation_id": "c1"}),
        json.dumps(
            {
                "event": "step_update",
                "step_update": {
                    "step_index": 1,
                    "state": "DONE",
                    "step_type": "agent_response",
                    "text_delta": "first",
                },
            }
        ),
        json.dumps({"event": "result", "result": {"status": "SUCCESS"}}),
    ]
    spawn = FakeSpawn(FakeProcess(lines))
    events = _adapter(spawn).start(make_request(spec=PRO_HIGH)).events()

    assert next(events) == Message("first")
    events.close()

    process = spawn.processes[0]
    assert process.stopped_early
    assert process.lines_read == 2
