from __future__ import annotations

import _thread
import contextlib
import json
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

import pytest
from pydantic import BaseModel
from tests.agents.fakes import (
    RUNNER_SPEC,
    Call,
    FakeAdapter,
    Turn,
    final,
    make_task,
)
from tests.agents.fakes import make_runner as build_runner
from tests.fakes import frames_tool

from grillmaster.agents.adapters.base import (
    Capability,
    MediaDelivery,
    SchemaDelivery,
    ToolImageDelivery,
    TurnDefect,
)
from grillmaster.agents.errors import (
    AgentCancelledError,
    AgentConfigError,
    AgentError,
    AgentInputError,
    AgentOutputError,
    AgentQuotaError,
    AgentTransientError,
    ValidationFailure,
    classify_failure,
)
from grillmaster.agents.events import Message, Thought, ToolCall, ToolResult
from grillmaster.agents.prompt import AUDIO_UNAVAILABLE_MARKER
from grillmaster.agents.runner import MAX_FRAME_TURNS, AgentRunner, SessionRecord
from grillmaster.agents.task import (
    AgentJob,
    AgentResult,
    AgentTask,
    FilesOutput,
    SchemaOutput,
    TextOutput,
)
from grillmaster.core.model_spec import Backend, Effort, ModelSpec, Role
from grillmaster.core.process import ABORT, ProcessAbortedError, kill_all
from grillmaster.core.tool_session import (
    FramesTool,
    ToolSession,
    leave_pending_frames,
)
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

    from grillmaster.agents.adapters.base import TurnRequest

SPEC = RUNNER_SPEC


class Answer(BaseModel):
    color: str


class WaitLog(threading.Event):
    """An abort event whose waits are recorded (`on_wait`) instead of slept."""

    def __init__(self, on_wait: Callable[[float], object]) -> None:
        super().__init__()
        self._on_wait = on_wait

    @override
    def wait(self, timeout: float | None = None) -> bool:
        assert timeout is not None
        self._on_wait(timeout)
        return self.is_set()


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def make_runner(
    recording_sink: RecordingSink, sleeps: list[float]
) -> Callable[..., AgentRunner]:
    def make(adapter: FakeAdapter, **overrides: Any) -> AgentRunner:
        options: dict[str, Any] = {"abort": WaitLog(sleeps.append), **overrides}
        return build_runner(adapter, events=recording_sink, **options)

    return make


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
    assert "10.000 秒與70.500 秒之間" in message
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
        (Capability.SCHEMA_OUTPUT, {"output": SchemaOutput(Answer)}),
        (Capability.RESUME, {}),
        (Capability.MCP, {"tools": "frames"}),
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
        overrides["tools"] = _frames_tools(tmp_path)
    overrides.setdefault("output", TextOutput())
    if "requires" in overrides:
        overrides["requires"] = frozenset(overrides["requires"])
    adapter = FakeAdapter(capabilities=frozenset(Capability) - {missing})
    with pytest.raises(AgentConfigError, match=str(missing)):
        make_runner(adapter).run(make_task(tmp_path, **overrides))
    assert adapter.calls == []
    assert recording_sink.events == []


@pytest.mark.parametrize("missing", [Capability.RESUME, Capability.IMAGE_INPUT])
def test_frames_for_the_next_message_need_a_resumed_turn_with_images(
    missing: Capability, tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    adapter = FakeAdapter(
        capabilities=frozenset(Capability) - {missing},
        tool_image_delivery=ToolImageDelivery.NEXT_MESSAGE,
    )
    task = make_task(
        tmp_path, TextOutput(), tools=_frames_tools(tmp_path), max_repairs=0
    )
    with pytest.raises(AgentConfigError, match=str(missing)):
        make_runner(adapter).run(task)


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
    # Both turns ran in one adapter session, closed once it ended.
    (session,) = adapter.sessions
    assert session.closes == 1


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
    assert "沒有收到 JSON 結果" in adapter.calls[1].request.message


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
    assert [session.closes for session in adapter.sessions] == [1]
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


def test_files_output_optional_files_are_removed_before_an_attempt(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    workdir = tmp_path / "refine" / "work"
    workdir.mkdir(parents=True)
    stale_report = workdir / "report.md"
    stale_report.write_text("old", encoding="utf-8")

    def write_srt(request: Any) -> None:
        (request.workdir / "refined.srt").write_text("new", encoding="utf-8")

    task = make_task(
        tmp_path,
        FilesOutput((Path("refined.srt"),), optional=(Path("report.md"),)),
    )
    result = make_runner(FakeAdapter([Turn(final=final(), on_start=write_srt)])).run(
        task
    )

    assert result.output == (workdir.resolve() / "refined.srt",)
    assert not stale_report.exists()


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


def _frames_tools(tmp_path: Path) -> ToolSession:
    return ToolSession(
        project_root=tmp_path, frames=frames_tool(tmp_path), check_srt=None
    )


def _ask_for_frames(*frames: Path) -> Callable[[TurnRequest], None]:
    """A turn hook playing the tool server: it leaves `frames` pending."""

    def leave(request: TurnRequest) -> None:
        manifest = ToolSession.load(request.session_dir / "tools.json")
        assert manifest.frames is not None
        assert manifest.frames.pending_frames is not None
        leave_pending_frames(manifest.frames.pending_frames, frames)

    return leave


def test_requested_frames_come_with_the_next_message_without_a_repair(
    tmp_path: Path,
    make_runner: Callable[..., AgentRunner],
    recording_sink: RecordingSink,
):
    frame = tmp_path / "frames" / "f1.jpg"
    adapter = FakeAdapter(
        [
            Turn(final=final("waiting for frames"), on_start=_ask_for_frames(frame)),
            Turn(final=final("seen")),
        ],
        tool_image_delivery=ToolImageDelivery.NEXT_MESSAGE,
    )
    task = make_task(tmp_path, TextOutput(), tools=_frames_tools(tmp_path))
    result = make_runner(adapter).run(task)

    assert (result.output, result.repairs) == ("seen", 0)
    start, frames_turn = adapter.calls
    assert "附在下一則訊息" in start.request.message
    assert frames_turn.kind == "resume"
    assert frames_turn.request.images == (frame,)
    assert frames_turn.request.message.startswith("【畫面】")
    assert not (tmp_path / "refine" / "session" / "frames_pending.txt").exists()
    assert (ActivityKind.TOOL_RESULT, "get_frames: 1 frame(s) attached") in activities(
        recording_sink
    )


def test_frame_turns_are_recorded_in_the_session_record(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    frame = tmp_path / "frames" / "f1.jpg"
    adapter = FakeAdapter(
        [
            Turn(final=final("waiting"), on_start=_ask_for_frames(frame)),
            Turn(final=final("seen")),
        ],
        tool_image_delivery=ToolImageDelivery.NEXT_MESSAGE,
    )
    task = make_task(tmp_path, TextOutput(), tools=_frames_tools(tmp_path))
    make_runner(adapter).run(task)
    record = SessionRecord.model_validate_json(
        (tmp_path / "refine" / "session" / "result.json").read_text(encoding="utf-8")
    )
    assert (record.frame_turns, record.repairs) == (1, 0)


def test_inline_tool_images_leave_the_manifest_untouched(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    tools = _frames_tools(tmp_path)
    adapter = FakeAdapter([Turn(final=final())])
    make_runner(adapter).run(make_task(tmp_path, TextOutput(), tools=tools))
    manifest = ToolSession.load(tmp_path / "refine" / "session" / "tools.json")
    assert manifest == tools


def test_a_model_that_keeps_asking_for_frames_fails(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    frame = tmp_path / "frames" / "f1.jpg"

    def script(_call: Call) -> Turn:
        return Turn(final=final("more"), on_start=_ask_for_frames(frame))

    adapter = FakeAdapter(
        script=script, tool_image_delivery=ToolImageDelivery.NEXT_MESSAGE
    )
    task = make_task(tmp_path, TextOutput(), tools=_frames_tools(tmp_path))
    with pytest.raises(AgentOutputError, match="still asking for frames"):
        make_runner(adapter).run(task)
    assert len(adapter.calls) == MAX_FRAME_TURNS + 1


@pytest.mark.parametrize(
    ("delivery", "output", "expected"),
    [
        pytest.param(
            SchemaDelivery.FINISH_TOOL, SchemaOutput(Answer), "finish", id="finish"
        ),
        pytest.param(
            SchemaDelivery.PROMPT, SchemaOutput(Answer), "schema", id="prompt"
        ),
        pytest.param(SchemaDelivery.NATIVE, SchemaOutput(Answer), None, id="native"),
        pytest.param(SchemaDelivery.FINISH_TOOL, TextOutput(), None, id="no-schema"),
        pytest.param(SchemaDelivery.PROMPT, TextOutput(), None, id="no-schema-prompt"),
    ],
)
def test_submit_instructions_follow_the_schema_delivery(
    tmp_path: Path,
    make_runner: Callable[..., AgentRunner],
    delivery: SchemaDelivery,
    output: Any,
    expected: str | None,
):
    adapter = FakeAdapter(
        [Turn(final=final('{"color": "red"}', structured={"color": "red"}))],
        schema_delivery=delivery,
    )
    make_runner(adapter).run(make_task(tmp_path, output))
    message = adapter.calls[0].request.message
    assert ("【提交結果】" in message) is (expected is not None)
    assert ("finish 工具" in message) is (expected == "finish")
    assert ("JSON Schema" in message) is (expected == "schema")
    if expected == "schema":
        assert '"color"' in message


def test_a_prompt_backend_answer_is_read_from_its_final_message(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    text = 'Looked at it.\n```json\n{"color": "red"}\n```'
    adapter = FakeAdapter(
        [Turn(final=final(text))], schema_delivery=SchemaDelivery.PROMPT
    )
    result = make_runner(adapter).run(make_task(tmp_path, SchemaOutput(Answer)))
    assert result.output == Answer(color="red")


def test_the_audio_unavailable_marker_stops_the_session(
    tmp_path: Path,
    make_runner: Callable[..., AgentRunner],
    recording_sink: RecordingSink,
):
    audio = tmp_path / "chunk.ogg"
    audio.write_bytes(b"x")
    turn = Turn(
        events=[
            Message(f"{AUDIO_UNAVAILABLE_MARKER}: only a file name came back"),
            Message("never read"),
        ],
        final=final("guessed anyway"),
    )
    adapter = FakeAdapter([turn])
    task = make_task(tmp_path, TextOutput(), audio=(audio,))

    with pytest.raises(AgentInputError, match="only a file name came back"):
        make_runner(adapter).run(task)

    assert turn.stopped
    assert [call.kind for call in adapter.calls] == ["start"]
    assert AUDIO_UNAVAILABLE_MARKER in adapter.calls[0].request.message
    record = SessionRecord.model_validate_json(
        (tmp_path / "refine" / "session" / "result.json").read_text(encoding="utf-8")
    )
    assert record.outcome is SessionOutcome.INPUT_ERROR
    assert (
        ActivityKind.MESSAGE,
        f"{AUDIO_UNAVAILABLE_MARKER}: only a file name came back",
    ) in activities(recording_sink)


def test_the_audio_marker_means_nothing_without_audio(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    text = f"{AUDIO_UNAVAILABLE_MARKER}: quoted"
    adapter = FakeAdapter([Turn(events=[Message(text)], final=final(text))])
    result = make_runner(adapter).run(make_task(tmp_path, TextOutput()))
    assert result.output == text
    assert AUDIO_UNAVAILABLE_MARKER not in adapter.calls[0].request.message


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


def jobs_of[T](
    tasks: list[AgentTask[T]], accepted: list[AgentResult[T]]
) -> list[AgentJob[T]]:
    return [
        AgentJob(task.name, prepare=lambda task=task: task, accept=accepted.append)
        for task in tasks
    ]


def last_line(call: Call) -> str:
    return call.request.message.split("\n")[-1]


def test_run_jobs_respects_the_global_limit_and_accepts_each_result(
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

        return Turn(final=final(last_line(call)), on_start=enter)

    tasks = [
        make_task(tmp_path, TextOutput(), name=f"chunks/{i}", prompt=f"chunk-{i}")
        for i in range(6)
    ]
    accepted: list[AgentResult[str]] = []
    runner = make_runner(FakeAdapter(script=script), max_concurrent=2)

    assert runner.run_jobs(jobs_of(tasks, accepted)) == []
    assert peak == 2
    assert sorted(result.output for result in accepted) == [
        f"chunk-{i}" for i in range(6)
    ]


def test_run_jobs_returns_failures_in_order_and_keeps_the_rest(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    def script(call: Call) -> Turn:
        if "bad" in call.request.message:
            return Turn(error=AgentQuotaError("429"))
        return Turn(final=final("ok"))

    tasks = [
        make_task(tmp_path, TextOutput(), name="a", prompt="bad"),
        make_task(tmp_path, TextOutput(), name="b", prompt="good"),
    ]
    accepted: list[AgentResult[str]] = []
    failures = make_runner(FakeAdapter(script=script)).run_jobs(
        jobs_of(tasks, accepted)
    )

    assert [failure.name for failure in failures] == ["a"]
    assert isinstance(failures[0].error, AgentQuotaError)
    assert str(failures[0]) == "a: 429"
    assert [result.output for result in accepted] == ["ok"]


def test_a_failing_prepare_fails_only_its_job(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    def broken_prepare() -> AgentTask[str]:
        raise OSError("ffmpeg crashed")

    accepted: list[AgentResult[str]] = []
    good = make_task(tmp_path, TextOutput(), name="good")
    jobs = [
        AgentJob("broken", prepare=broken_prepare, accept=accepted.append),
        *jobs_of([good], accepted),
    ]
    adapter = FakeAdapter(script=lambda call: Turn(final=final("ok")))

    failures = make_runner(adapter).run_jobs(jobs)

    assert [(f.name, type(f.error)) for f in failures] == [("broken", OSError)]
    assert [result.output for result in accepted] == ["ok"]
    assert len(adapter.calls) == 1


def test_a_failing_accept_fails_only_its_job(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    accepted: list[str] = []

    def accept(result: AgentResult[str]) -> None:
        if result.output == "bad":
            raise ValueError("disk full")
        accepted.append(result.output)

    tasks = [
        make_task(tmp_path, TextOutput(), name=name, prompt=name)
        for name in ("bad", "good")
    ]
    jobs = [AgentJob(t.name, prepare=lambda t=t: t, accept=accept) for t in tasks]
    adapter = FakeAdapter(script=lambda call: Turn(final=final(last_line(call))))

    failures = make_runner(adapter).run_jobs(jobs)

    assert [(f.name, str(f.error)) for f in failures] == [("bad", "disk full")]
    assert accepted == ["good"]


def test_an_interrupt_propagates_after_the_running_job_is_accepted(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    """A Ctrl-C reaching the waiting thread cancels the jobs not yet started
    and propagates once the running job has accepted its result."""
    started = threading.Event()
    accepted: list[str] = []
    tasks = [
        make_task(tmp_path, TextOutput(), name=name, prompt=name)
        for name in ("first", "second")
    ]

    def enter(request: Any) -> None:
        started.set()
        time.sleep(1.0)

    adapter = FakeAdapter(
        script=lambda call: Turn(final=final(last_line(call)), on_start=enter)
    )
    jobs = [
        AgentJob(
            t.name,
            prepare=lambda t=t: t,
            accept=lambda result: accepted.append(result.output),
        )
        for t in tasks
    ]

    def interrupt() -> None:
        started.wait(5)
        _thread.interrupt_main()

    threading.Thread(target=interrupt).start()
    with pytest.raises(KeyboardInterrupt):
        make_runner(adapter, max_concurrent=1).run_jobs(jobs)

    assert accepted == ["first"]
    assert len(adapter.calls) == 1


def test_run_jobs_carries_the_stage_scope_into_workers(
    tmp_path: Path,
    make_runner: Callable[..., AgentRunner],
    recording_sink: RecordingSink,
):
    adapter = FakeAdapter(script=lambda call: Turn(final=final()))
    tasks = [make_task(tmp_path, TextOutput(), name=f"t{i}") for i in range(3)]
    with stage_scope("chunks"):
        make_runner(adapter).run_jobs(jobs_of(tasks, []))
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

    failures = make_runner(FakeAdapter(script=script)).run_jobs(jobs_of(tasks, []))
    errors = [f.error for f in failures if isinstance(f.error, AgentConfigError)]
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
    make_runner(FakeAdapter(script=script), max_concurrent=1).run_jobs(
        jobs_of(tasks, [])
    )
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

    runner = make_runner(adapter, max_concurrent=1, abort=WaitLog(sleep))
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


# --- abort and quota ----------------------------------------------------------------


def test_an_abort_wakes_the_retry_wait_and_starts_no_new_attempt(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    abort = WaitLog(lambda seconds: abort.set())
    adapter = FakeAdapter(script=lambda call: Turn(error=AgentTransientError("killed")))
    runner = make_runner(adapter, abort=abort)
    with pytest.raises(AgentCancelledError, match="aborted before a session"):
        runner.run(make_task(tmp_path, TextOutput(), attempts=3))
    assert len(adapter.calls) == 1


def test_a_real_retry_wait_returns_at_once_on_abort(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    abort = threading.Event()
    adapter = FakeAdapter(script=lambda call: Turn(error=AgentTransientError("killed")))
    runner = make_runner(adapter, abort=abort, retry_delay_s=60.0)
    threading.Timer(0.2, abort.set).start()
    began = time.monotonic()
    with pytest.raises(AgentCancelledError):
        runner.run(make_task(tmp_path, TextOutput(), attempts=2))
    assert time.monotonic() - began < 10
    assert len(adapter.calls) == 1


def test_kill_all_latches_the_abort_for_every_runner(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    adapter = FakeAdapter([Turn(final=final("ok"))])
    runner = make_runner(adapter, abort=ABORT)
    kill_all()
    with pytest.raises(AgentCancelledError):
        runner.run(make_task(tmp_path, TextOutput()))
    assert adapter.calls == []


def test_a_child_refused_by_the_abort_latch_cancels_the_task(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    adapter = FakeAdapter([Turn(error=ProcessAbortedError("abort in progress"))])
    with pytest.raises(AgentCancelledError, match="before a turn"):
        make_runner(adapter).run(make_task(tmp_path, TextOutput(), attempts=3))
    assert len(adapter.calls) == 1


def test_an_abort_starts_no_repair_turn(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    abort = threading.Event()
    adapter = FakeAdapter([Turn(final=final(""), on_start=lambda r: abort.set())])
    runner = make_runner(adapter, abort=abort)
    with pytest.raises(AgentCancelledError, match="before a repair"):
        runner.run(make_task(tmp_path, TextOutput()))
    assert [call.kind for call in adapter.calls] == ["start"]
    record = SessionRecord.model_validate_json(
        (tmp_path / "refine" / "session" / "result.json").read_text(encoding="utf-8")
    )
    assert record.error is not None
    assert record.error.startswith("AgentCancelledError")
    assert record.outcome is SessionOutcome.CANCELLED


def test_a_quota_error_fails_the_backend_s_later_tasks_at_once(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    tasks = [
        make_task(tmp_path, TextOutput(), name=name, prompt=name)
        for name in ("first", "second", "third")
    ]
    adapter = FakeAdapter(
        script=lambda call: Turn(error=AgentQuotaError("429 usage limit"))
    )
    runner = make_runner(adapter, max_concurrent=1)
    failures = runner.run_jobs(jobs_of(tasks, []))

    assert len(adapter.calls) == 1
    assert [f.name for f in failures] == ["first", "second", "third"]
    assert all(isinstance(f.error, AgentQuotaError) for f in failures)
    assert "not started" in str(failures[1].error)
    # The latch outlives the batch: a later task of that backend fails too.
    with pytest.raises(AgentQuotaError, match="hit its quota earlier"):
        runner.run(make_task(tmp_path, TextOutput(), name="later"))
    assert len(adapter.calls) == 1


def test_a_rate_limit_is_retried_and_does_not_latch_the_backend(
    tmp_path: Path, make_runner: Callable[..., AgentRunner], sleeps: list[float]
):
    rate_limited = classify_failure(
        "429 Too Many Requests, rate limit, retry later", login_hint="x"
    )
    adapter = FakeAdapter(
        [
            Turn(error=rate_limited),
            Turn(final=final("first")),
            Turn(final=final("later")),
        ]
    )
    runner = make_runner(adapter)

    first = runner.run(make_task(tmp_path, TextOutput(), name="first", attempts=2))
    later = runner.run(make_task(tmp_path, TextOutput(), name="later"))

    assert (first.output, first.attempt, later.output) == ("first", 2, "later")
    assert sleeps == [7.0]


def test_a_quota_error_leaves_other_backends_running(
    tmp_path: Path,
    recording_sink: RecordingSink,
):
    codex = FakeAdapter(script=lambda call: Turn(error=AgentQuotaError("429")))
    agy = FakeAdapter(backend=Backend.AGY, script=lambda call: Turn(final=final("ok")))
    adapters = {codex.backend: codex, agy.backend: agy}
    roles = {
        Role.POSTPROCESS: SPEC,
        Role.CHUNK: ModelSpec(Backend.AGY, "gemini-test", Effort.HIGH),
    }
    runner = AgentRunner(
        roles,
        adapters.__getitem__,
        max_concurrent=1,
        timeout_s=60.0,
        events=recording_sink,
        abort=threading.Event(),
    )
    with pytest.raises(AgentQuotaError):
        runner.run(make_task(tmp_path, TextOutput(), name="codex"))
    result = runner.run(make_task(tmp_path, TextOutput(), name="agy", role=Role.CHUNK))
    assert result.output == "ok"


def test_an_interrupt_latches_the_abort_so_a_running_job_does_not_retry(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    """Ctrl-C kills the running CLI (a transient error); the job must not
    start a fresh session afterwards."""
    started = threading.Event()

    def enter(request: Any) -> None:
        started.set()
        time.sleep(0.5)

    adapter = FakeAdapter(
        script=lambda call: Turn(error=AgentTransientError("killed"), on_start=enter)
    )
    tasks = [make_task(tmp_path, TextOutput(), name="only", attempts=3)]

    def interrupt() -> None:
        started.wait(5)
        _thread.interrupt_main()

    threading.Thread(target=interrupt).start()
    abort = threading.Event()
    runner = make_runner(adapter, max_concurrent=1, abort=abort)
    with pytest.raises(KeyboardInterrupt):
        runner.run_jobs(jobs_of(tasks, []))

    assert abort.is_set()
    assert len(adapter.calls) == 1


def test_a_failed_temp_workdir_cleanup_does_not_fail_the_session(
    tmp_path: Path, make_runner: Callable[..., AgentRunner]
):
    held: list[Any] = []

    def hold_a_file(request: Any) -> None:
        # Windows refuses to delete a file that is still open.
        held.append((request.workdir / "busy.log").open("w"))

    adapter = FakeAdapter([Turn(final=final("ok"), on_start=hold_a_file)])
    try:
        result = make_runner(adapter).run(
            make_task(tmp_path, TextOutput(), workdir=None)
        )
    finally:
        for handle in held:
            handle.close()
    assert result.output == "ok"
