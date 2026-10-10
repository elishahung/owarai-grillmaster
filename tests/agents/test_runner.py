from __future__ import annotations

import contextlib
import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel
from tests.agents.fakes import Call, FakeAdapter, Turn, final

from grillmaster.agents.adapters.base import Capability, MediaDelivery, TurnDefect
from grillmaster.agents.errors import (
    AgentConfigError,
    AgentError,
    AgentOutputError,
    AgentQuotaError,
    AgentTransientError,
    ValidationFailure,
)
from grillmaster.agents.events import Message, Thought, ToolCall, ToolResult
from grillmaster.agents.runner import AgentRunner, SessionRecord
from grillmaster.agents.task import (
    AgentTask,
    FilesOutput,
    OutputSpec,
    SchemaOutput,
    TextOutput,
)
from grillmaster.core.model_spec import Backend, Effort, ModelSpec, Role
from grillmaster.core.tool_session import FramesTool, ToolSession
from grillmaster.events.context import stage_scope
from grillmaster.events.types import (
    ActivityKind,
    AgentActivity,
    AgentSessionFinished,
    AgentSessionStarted,
    SessionOutcome,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.fakes import RecordingSink

SPEC = ModelSpec(Backend.CODEX, "gpt-test", Effort.HIGH)
ROLES = dict.fromkeys(Role, SPEC)


class Answer(BaseModel):
    color: str


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def make_runner(
    recording_sink: RecordingSink, sleeps: list[float]
) -> Callable[..., AgentRunner]:
    def make(adapter: FakeAdapter, **overrides: Any) -> AgentRunner:
        options: dict[str, Any] = {
            "max_concurrent": 2,
            "timeout_s": 60.0,
            "events": recording_sink,
            "tool_server": ("python", "-m", "grillmaster.agent_tools"),
            "retry_delay_s": 7.0,
            "clock": Clock(),
            "sleep": sleeps.append,
        }
        options.update(overrides)
        roles = options.pop("roles", ROLES)
        return AgentRunner(roles, {adapter.backend: adapter}.__getitem__, **options)

    return make


def make_task[T](
    tmp_path: Path,
    output: OutputSpec[T],
    *,
    name: str = "refine",
    **overrides: Any,
) -> AgentTask[T]:
    fields: dict[str, Any] = {
        "name": name,
        "role": Role.POSTPROCESS,
        "instructions": "INSTRUCTIONS",
        "prompt": "PROMPT",
        "session_dir": tmp_path / name / "session",
        "workdir": tmp_path / name / "work",
        "output": output,
    }
    fields.update(overrides)
    return AgentTask(**fields)


def activities(sink: RecordingSink) -> list[tuple[ActivityKind, str]]:
    return [(e.kind, e.summary) for e in sink.events if isinstance(e, AgentActivity)]


# --- happy path, events, session files --------------------------------------------


def test_schema_task_returns_the_parsed_model(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    adapter = FakeAdapter(
        [
            Turn(
                final=final(
                    "{}", structured={"color": "red"}, usage={"input_tokens": 5}
                )
            )
        ]
    )
    result = make_runner(adapter).run(make_task(tmp_path, SchemaOutput(Answer)))

    assert result.output == Answer(color="red")
    assert result.session_id == "sess-1"
    assert result.spec == SPEC
    assert (result.repairs, result.attempt) == (0, 1)
    assert result.usage == {"input_tokens": 5}
    request = adapter.calls[0].request
    assert request.schema is not None
    assert request.schema["required"] == ["color"]
    assert request.schema["additionalProperties"] is False
    schema_path = result.session_dir / "schema.json"
    assert request.schema_path == schema_path
    assert json.loads(schema_path.read_text(encoding="utf-8")) == request.schema


def test_events_are_scoped_and_ordered(
    tmp_path: Path,
    make_runner: Callable[..., AgentRunner],
    recording_sink: RecordingSink,
):
    turn = Turn(
        events=[
            Thought("Checking the name\nmore"),
            Message("I will look at frames"),
            ToolCall("get_frames", {"times": [62.5, 70, 77]}),
            ToolResult("get_frames"),
            Message("final answer text"),
        ],
        final=final("final answer text"),
    )
    with stage_scope("refine"):
        make_runner(FakeAdapter([turn])).run(make_task(tmp_path, TextOutput()))

    events = recording_sink.events
    assert events[0] == AgentSessionStarted(
        task="refine", stage="refine", backend="codex", model="gpt-test", effort="high"
    )
    assert activities(recording_sink) == [
        (ActivityKind.THOUGHT, "Checking the name"),
        (ActivityKind.MESSAGE, "I will look at frames"),
        (ActivityKind.TOOL_CALL, "get_frames 62.5, 70, 77"),
        (ActivityKind.TOOL_RESULT, "get_frames ok"),
        # The last message is the final output: length only.
        (ActivityKind.MESSAGE, "final output (17 chars)"),
    ]
    finished = events[-1]
    assert isinstance(finished, AgentSessionFinished)
    assert finished.outcome is SessionOutcome.OK
    assert finished.repairs == 0


def test_session_directory_contents(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    tools = ToolSession(
        project_root=tmp_path,
        frames=FramesTool(
            video=tmp_path / "v.mp4",
            frames_dir=tmp_path / "frames",
            window=(10.0, 70.5),
            max_side=768,
        ),
        check_srt=None,
    )
    adapter = FakeAdapter([Turn(final=final("ok"), raw=['{"a":1}', '{"b":2}'])])
    task = make_task(tmp_path, TextOutput(), tools=tools)
    make_runner(adapter).run(task)

    session = tmp_path / "refine" / "session"
    message = (session / "prompt.md").read_text(encoding="utf-8")
    assert message.startswith("INSTRUCTIONS\n\nPROMPT\n\n【可用工具】")
    assert "get_frames(times)" in message
    assert "10 秒與70.5 秒之間" in message
    assert ToolSession.load(session / "tools.json") == tools
    assert (session / "raw.jsonl").read_text(encoding="utf-8") == '{"a":1}\n{"b":2}\n'
    record = SessionRecord.model_validate_json(
        (session / "result.json").read_text(encoding="utf-8")
    )
    assert record.outcome is SessionOutcome.OK
    assert record.session_id == "sess-1"
    assert (record.backend, record.model, record.effort) == (
        "codex",
        "gpt-test",
        "high",
    )
    mcp = adapter.calls[0].request.mcp
    assert mcp is not None
    assert mcp.command == "python"
    assert mcp.args == (
        "-m",
        "grillmaster.agent_tools",
        "--session",
        str(session / "tools.json"),
    )


def test_stale_session_files_are_cleared(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    session = tmp_path / "refine" / "session"
    session.mkdir(parents=True)
    (session / "raw.jsonl").write_text("old\n", encoding="utf-8")
    (session / "tools.json").write_text("{}", encoding="utf-8")
    make_runner(FakeAdapter([Turn(final=final())])).run(
        make_task(tmp_path, TextOutput())
    )
    assert (session / "raw.jsonl").read_text(encoding="utf-8") == ""
    assert not (session / "tools.json").exists()


def test_without_workdir_a_temp_dir_is_used_and_removed(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    seen: list[Path] = []

    def check(request: Any) -> None:
        seen.append(request.workdir)
        assert request.workdir.is_dir()

    adapter = FakeAdapter([Turn(final=final(), on_start=check)])
    make_runner(adapter).run(make_task(tmp_path, TextOutput(), workdir=None))
    assert not seen[0].exists()
    assert tmp_path not in seen[0].parents


# --- capability and configuration checks -------------------------------------------


@pytest.mark.parametrize(
    ("missing", "overrides"),
    [
        (Capability.AUDIO_INPUT, {"audio": ("a.ogg",)}),
        (Capability.IMAGE_INPUT, {"images": ("a.png",)}),
        (Capability.NATIVE_SCHEMA, {"output": SchemaOutput(Answer)}),
        (Capability.RESUME, {}),
        (Capability.MCP, {"tools": "frames"}),
        (Capability.MCP_IMAGE_RESULT, {"tools": "frames"}),
        (Capability.IMAGE_GENERATION, {"requires": {Capability.IMAGE_GENERATION}}),
        (Capability.WEB_SEARCH, {"requires": {Capability.WEB_SEARCH}}),
    ],
)
def test_missing_capability_fails_before_any_session(
    missing: Capability,
    overrides: dict[str, Any],
    tmp_path: Path,
    make_runner: Callable[..., AgentRunner],
    recording_sink: RecordingSink,
):
    for key in ("audio", "images"):
        if key in overrides:
            path = tmp_path / overrides[key][0]
            path.write_bytes(b"x")
            overrides[key] = (path,)
    if overrides.get("tools") == "frames":
        overrides["tools"] = ToolSession(
            project_root=tmp_path,
            frames=FramesTool(
                video=tmp_path / "v.mp4",
                frames_dir=tmp_path / "f",
                window=(0.0, None),
                max_side=768,
            ),
            check_srt=None,
        )
    overrides.setdefault("output", TextOutput())
    if "requires" in overrides:
        overrides["requires"] = frozenset(overrides["requires"])
    adapter = FakeAdapter(capabilities=frozenset(Capability) - {missing})
    with pytest.raises(AgentConfigError, match=str(missing)):
        make_runner(adapter).run(make_task(tmp_path, **overrides))
    assert adapter.calls == []
    assert recording_sink.events == []


def test_preflight_failure_happens_before_any_session(
    tmp_path: Path,
    make_runner: Callable[..., AgentRunner],
    recording_sink: RecordingSink,
):
    image = tmp_path / "frame.png"
    image.write_bytes(b"x")
    adapter = FakeAdapter(preflight_error=AgentConfigError("agy has no model"))
    task = make_task(tmp_path, TextOutput(), images=(image,))
    with pytest.raises(AgentConfigError, match="no model"):
        make_runner(adapter).run(task)
    assert adapter.preflights == [(SPEC, (image.resolve(),), ())]
    assert adapter.calls == []
    assert recording_sink.events == []
    assert not (tmp_path / "refine").exists()


def test_web_search_reaches_the_request_only_when_required(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    adapter = FakeAdapter(script=lambda call: Turn(final=final()))
    runner = make_runner(adapter)
    runner.run(make_task(tmp_path, TextOutput(), name="plain"))
    runner.run(
        make_task(
            tmp_path,
            TextOutput(),
            name="research",
            requires=frozenset({Capability.WEB_SEARCH}),
        )
    )
    assert [call.request.web_search for call in adapter.calls] == [False, True]


def test_missing_role_is_a_config_error(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    runner = make_runner(FakeAdapter(), roles={Role.CHUNK: SPEC})
    with pytest.raises(AgentConfigError, match="role postprocess"):
        runner.run(make_task(tmp_path, TextOutput()))


def test_missing_input_file_is_a_config_error(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    task = make_task(tmp_path, TextOutput(), images=(tmp_path / "nope.png",))
    with pytest.raises(AgentConfigError, match=r"nope\.png"):
        make_runner(FakeAdapter()).run(task)


def test_free_key_dict_schema_is_a_config_error(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    class Loose(BaseModel):
        terms: dict[str, str]

    with pytest.raises(AgentConfigError, match="free-key"):
        make_runner(FakeAdapter()).run(make_task(tmp_path, SchemaOutput(Loose)))


# --- repair ---------------------------------------------------------------------------


def test_validation_failure_resumes_the_same_session_with_only_the_message(
    tmp_path: Path,
    make_runner: Callable[..., AgentRunner],
    recording_sink: RecordingSink,
):
    image = tmp_path / "frame.png"
    image.write_bytes(b"x")
    adapter = FakeAdapter(
        [
            Turn(final=final(structured={"color": "red"}, session_id="s-1")),
            Turn(final=final(structured={"color": "BLUE"}, session_id="s-1")),
        ]
    )

    def must_be_upper(answer: Answer) -> None:
        if answer.color != answer.color.upper():
            raise ValidationFailure("color must be UPPERCASE")

    task = make_task(
        tmp_path, SchemaOutput(Answer), validate=must_be_upper, images=(image,)
    )
    result = make_runner(adapter).run(task)

    assert result.output == Answer(color="BLUE")
    assert result.repairs == 1
    start, repair = adapter.calls
    assert (start.kind, repair.kind, repair.session_id) == ("start", "resume", "s-1")
    assert "color must be UPPERCASE" in repair.request.message
    assert "INSTRUCTIONS" not in repair.request.message
    assert repair.request.images == ()
    assert repair.request.schema == start.request.schema
    assert repair.request.workdir == start.request.workdir
    assert (ActivityKind.REPAIR, "color must be UPPERCASE") in activities(
        recording_sink
    )


def test_missing_structured_output_is_repaired(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    adapter = FakeAdapter(
        [
            Turn(final=final("prose", structured=None)),
            Turn(final=final(structured={"color": "red"})),
        ]
    )
    result = make_runner(adapter).run(make_task(tmp_path, SchemaOutput(Answer)))
    assert result.output.color == "red"
    assert "結構化輸出" in adapter.calls[1].request.message


def test_repairs_exhausted_is_an_output_error_without_a_new_session(
    tmp_path: Path,
    make_runner: Callable[..., AgentRunner],
    recording_sink: RecordingSink,
    sleeps: list[float],
):
    adapter = FakeAdapter(script=lambda call: Turn(final=final(structured={"x": 1})))
    task = make_task(tmp_path, SchemaOutput(Answer), max_repairs=2, attempts=3)
    with pytest.raises(AgentOutputError, match="2 repair round"):
        make_runner(adapter).run(task)

    assert [call.kind for call in adapter.calls] == ["start", "resume", "resume"]
    assert sleeps == []
    finished = recording_sink.events[-1]
    assert isinstance(finished, AgentSessionFinished)
    assert finished.outcome is SessionOutcome.OUTPUT_ERROR
    record = json.loads(
        (tmp_path / "refine" / "session" / "result.json").read_text(encoding="utf-8")
    )
    assert record["outcome"] == "output_error"
    assert record["repairs"] == 2


def test_files_output_missing_file_is_repaired_and_stale_files_removed(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    workdir = tmp_path / "refine" / "work"
    workdir.mkdir(parents=True)
    stale = workdir / "refined.srt"
    stale.write_text("old", encoding="utf-8")

    def write_file(request: Any) -> None:
        (request.workdir / "refined.srt").write_text("new", encoding="utf-8")

    adapter = FakeAdapter(
        [
            Turn(final=final("report")),
            Turn(final=final("report"), on_start=write_file),
        ]
    )
    task = make_task(tmp_path, FilesOutput((Path("refined.srt"),)))
    result = make_runner(adapter).run(task)

    assert result.output == (workdir.resolve() / "refined.srt",)
    assert stale.read_text(encoding="utf-8") == "new"
    assert "refined.srt" in adapter.calls[1].request.message


def test_adapter_defects_are_repaired_resending_their_media(
    tmp_path: Path,
    make_runner: Callable[..., AgentRunner],
    recording_sink: RecordingSink,
):
    audio = tmp_path / "chunk.ogg"
    audio.write_bytes(b"x")
    resolved = audio.resolve()
    defect = TurnDefect("open the audio first", audio=(resolved,))
    adapter = FakeAdapter(
        [
            # A valid output does not outweigh the adapter's own finding.
            Turn(final=final("guessed", defects=(defect,))),
            Turn(final=final("heard it")),
        ],
        backend=Backend.AGY,
        media_delivery=MediaDelivery.VIEW_FILE,
    )
    roles = dict.fromkeys(Role, ModelSpec(Backend.AGY, "gemini-3.1-pro", Effort.HIGH))
    task = make_task(tmp_path, TextOutput(), audio=(audio,))
    result = make_runner(adapter, roles=roles).run(task)

    assert (result.output, result.repairs) == ("heard it", 1)
    start, repair = adapter.calls
    assert start.request.audio == (resolved,)
    assert str(resolved) in start.request.message  # view_file instructions
    assert repair.request.audio == (resolved,)
    assert "open the audio first" in repair.request.message
    assert (ActivityKind.REPAIR, "open the audio first") in activities(recording_sink)


def test_attached_media_are_not_listed_in_the_prompt(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    image = tmp_path / "poster.jpg"
    image.write_bytes(b"x")
    adapter = FakeAdapter([Turn(final=final())])
    make_runner(adapter).run(make_task(tmp_path, TextOutput(), images=(image,)))
    request = adapter.calls[0].request
    assert request.images == (image.resolve(),)
    assert "poster.jpg" not in request.message


# --- attempts and error classification ---------------------------------------------


def test_transient_errors_retry_in_a_fresh_session_after_a_delay(
    tmp_path: Path,
    make_runner: Callable[..., AgentRunner],
    recording_sink: RecordingSink,
    sleeps: list[float],
):
    adapter = FakeAdapter(
        [
            Turn(error=AgentTransientError("timed out")),
            Turn(final=final("second")),
        ]
    )
    result = make_runner(adapter).run(make_task(tmp_path, TextOutput(), attempts=2))

    assert result.output == "second"
    assert result.attempt == 2
    assert result.session_dir == (tmp_path / "refine" / "session.2").resolve()
    assert [call.kind for call in adapter.calls] == ["start", "start"]
    assert sleeps == [7.0]
    first = SessionRecord.model_validate_json(
        (tmp_path / "refine" / "session" / "result.json").read_text(encoding="utf-8")
    )
    assert first.outcome is SessionOutcome.TRANSIENT_ERROR
    assert first.error == "AgentTransientError: timed out"
    outcomes = [
        e.outcome for e in recording_sink.events if isinstance(e, AgentSessionFinished)
    ]
    assert outcomes == [SessionOutcome.TRANSIENT_ERROR, SessionOutcome.OK]


def test_transient_error_after_the_last_attempt_propagates(
    tmp_path: Path, make_runner: Callable[..., AgentRunner], sleeps: list[float]
):
    adapter = FakeAdapter(script=lambda call: Turn(error=AgentTransientError("crash")))
    with pytest.raises(AgentTransientError):
        make_runner(adapter).run(make_task(tmp_path, TextOutput(), attempts=3))
    assert len(adapter.calls) == 3
    assert sleeps == [7.0, 7.0]


@pytest.mark.parametrize(
    "error", [AgentQuotaError("429"), AgentConfigError("bad model")]
)
def test_non_transient_errors_are_not_retried(
    error: AgentError,
    tmp_path: Path,
    make_runner: Callable[..., AgentRunner],
    recording_sink: RecordingSink,
    sleeps: list[float],
):
    adapter = FakeAdapter(script=lambda call: Turn(error=error))
    with pytest.raises(type(error)):
        make_runner(adapter).run(make_task(tmp_path, TextOutput(), attempts=3))
    assert len(adapter.calls) == 1
    assert sleeps == []
    finished = recording_sink.events[-1]
    assert isinstance(finished, AgentSessionFinished)
    assert finished.outcome is error.outcome


def test_transient_error_during_repair_retries_from_a_fresh_session(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    adapter = FakeAdapter(
        [
            Turn(final=final("")),
            Turn(error=AgentTransientError("crash in repair")),
            Turn(final=final("fresh")),
        ]
    )
    result = make_runner(adapter).run(make_task(tmp_path, TextOutput(), attempts=2))
    assert result.output == "fresh"
    assert [call.kind for call in adapter.calls] == ["start", "resume", "start"]


# --- concurrency -----------------------------------------------------------------------


def test_run_many_respects_the_global_limit_and_keeps_order(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    lock = threading.Lock()
    active = 0
    peak = 0
    release = threading.Event()

    def script(call: Call) -> Turn:
        def enter(request: Any) -> None:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            release.wait(0.05)
            with lock:
                active -= 1

        name = call.request.message.split("\n")[-1]
        return Turn(final=final(name), on_start=enter)

    tasks = [
        make_task(tmp_path, TextOutput(), name=f"chunks/{i}", prompt=f"chunk-{i}")
        for i in range(6)
    ]
    results = make_runner(FakeAdapter(script=script), max_concurrent=2).run_many(tasks)

    assert peak == 2
    assert [r.output for r in results if not isinstance(r, AgentError)] == [
        f"chunk-{i}" for i in range(6)
    ]


def test_run_many_returns_failures_in_place(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    def script(call: Call) -> Turn:
        if "bad" in call.request.message:
            return Turn(error=AgentQuotaError("429"))
        return Turn(final=final("ok"))

    tasks = [
        make_task(tmp_path, TextOutput(), name="a", prompt="good"),
        make_task(tmp_path, TextOutput(), name="b", prompt="bad"),
    ]
    first, second = make_runner(FakeAdapter(script=script)).run_many(tasks)
    assert not isinstance(first, AgentError)
    assert first.output == "ok"
    assert isinstance(second, AgentQuotaError)


def test_run_many_carries_the_stage_scope_into_workers(
    tmp_path: Path,
    make_runner: Callable[..., AgentRunner],
    recording_sink: RecordingSink,
):
    adapter = FakeAdapter(script=lambda call: Turn(final=final()))
    tasks = [make_task(tmp_path, TextOutput(), name=f"t{i}") for i in range(3)]
    with stage_scope("chunks"):
        make_runner(adapter).run_many(tasks)
    stages = {
        e.stage for e in recording_sink.events if isinstance(e, AgentSessionStarted)
    }
    assert stages == {"chunks"}


def test_concurrent_tasks_may_not_share_a_workdir(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    tasks = [
        make_task(tmp_path, TextOutput(), name=name, workdir=tmp_path / "shared")
        for name in "ab"
    ]
    entered = threading.Barrier(2, timeout=0.5)

    def script(call: Call) -> Turn:
        def enter(request: Any) -> None:
            with contextlib.suppress(threading.BrokenBarrierError):
                entered.wait()

        return Turn(final=final("ok"), on_start=enter)

    results = make_runner(FakeAdapter(script=script)).run_many(tasks)
    errors = [r for r in results if isinstance(r, AgentConfigError)]
    assert len(errors) == 1
    assert "is in use by another running agent task" in str(errors[0])
    # Released afterwards: a later run may reuse it.
    assert make_runner(FakeAdapter([Turn(final=final("ok"))])).run(tasks[0])


def test_slot_is_held_across_repair_rounds(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    order: list[str] = []

    def script(call: Call) -> Turn:
        task = call.request.message.split("\n")[-1] if call.kind == "start" else None
        label = f"{call.kind}:{task or call.session_id}"
        order.append(label)
        if call.kind == "start":
            return Turn(final=final("", session_id=task or ""))
        return Turn(final=final("fixed", session_id=call.session_id or ""))

    tasks = [make_task(tmp_path, TextOutput(), name=name, prompt=name) for name in "ab"]
    make_runner(FakeAdapter(script=script), max_concurrent=1).run_many(tasks)
    # Each session's repair runs before the other session starts.
    assert order in (
        ["start:a", "resume:a", "start:b", "resume:b"],
        ["start:b", "resume:b", "start:a", "resume:a"],
    )


def test_slot_is_released_while_waiting_between_attempts(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    adapter = FakeAdapter(
        script=lambda call: Turn(
            error=AgentTransientError("crash")
            if "flaky" in call.request.message and len(adapter.calls) == 1
            else None,
            final=final("ok"),
        )
    )
    finished = threading.Event()
    runner: AgentRunner

    def sleep(seconds: float) -> None:
        # With one slot, this only completes if the waiting task released it.
        worker = threading.Thread(
            target=lambda: (
                runner.run(make_task(tmp_path, TextOutput(), name="other")),
                finished.set(),
            )
        )
        worker.start()
        worker.join(timeout=5)

    runner = make_runner(adapter, max_concurrent=1, sleep=sleep)
    runner.run(make_task(tmp_path, TextOutput(), prompt="flaky", attempts=2))
    assert finished.is_set()


def test_nested_run_is_refused(tmp_path: Path, make_runner: Callable[..., AgentRunner]):
    runner: AgentRunner
    nested: list[BaseException] = []

    def reenter(request: Any) -> None:
        try:
            runner.run(make_task(tmp_path, TextOutput(), name="inner"))
        except AgentConfigError as error:
            nested.append(error)

    runner = make_runner(FakeAdapter([Turn(final=final(), on_start=reenter)]))
    runner.run(make_task(tmp_path, TextOutput()))
    assert len(nested) == 1
    assert "nested" in str(nested[0])


def test_task_rejects_invalid_budgets(tmp_path: Path):
    with pytest.raises(ValueError, match="attempts"):
        make_task(tmp_path, TextOutput(), attempts=0)
    with pytest.raises(ValueError, match="max_repairs"):
        make_task(tmp_path, TextOutput(), max_repairs=-1)


def test_capabilities_of_a_role_come_from_its_backend(
    make_runner: Callable[..., AgentRunner],
):
    adapter = FakeAdapter([])
    runner = make_runner(adapter, roles={Role.CHUNK: SPEC})
    assert runner.capabilities(Role.CHUNK) == adapter.capabilities
    with pytest.raises(AgentConfigError, match="no model configured"):
        runner.capabilities(Role.PREPASS)
