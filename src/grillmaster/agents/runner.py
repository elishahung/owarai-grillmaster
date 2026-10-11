"""`AgentRunner`: the only way the pipeline talks to an agent.

Per task: resolve the role's `ModelSpec` and adapter, refuse what the adapter
cannot do (capabilities, then the adapter's own `preflight`), then run
attempts. Each attempt is one fresh session holding one slot of the global
concurrency limit from its first turn through every repair round; repair
rounds resume that session with only the repair message. Only
`AgentTransientError` starts another attempt, after a delay spent without a
slot. Every session leaves `prompt.md`, `tools.json`, `schema.json`,
`raw.jsonl` and `result.json` in its session directory.

Once the process-wide `core.process.ABORT` latch is set (an abort's
`kill_all`, an interrupt in `run_jobs`) no session or repair turn starts:
the retry delay wakes up and the next step raises `AgentCancelledError`.
Once a backend raised `AgentQuotaError`, every later task for that backend
fails at once with `AgentQuotaError` for the runner's lifetime; other
backends are unaffected.
"""

from __future__ import annotations

import contextvars
import json
import shutil
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from loguru import logger
from pydantic import BaseModel

from grillmaster.agents import prompt
from grillmaster.agents.adapters.base import (
    Capability,
    McpServer,
    SchemaDelivery,
    ToolImageDelivery,
    TurnDefect,
    TurnRequest,
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
)
from grillmaster.agents.events import (
    Message,
    first_line,
    summarize,
    summarize_final,
)
from grillmaster.agents.schema import (
    StrictSchemaError,
    json_object_answer,
    strict_json_schema,
)
from grillmaster.agents.task import (
    AgentResult,
    FilesOutput,
    JobFailure,
    SchemaOutput,
)
from grillmaster.core.fs import atomic_write_text
from grillmaster.core.json_artifact import write_model
from grillmaster.core.paths import attempt_path
from grillmaster.core.process import ABORT, ProcessAbortedError
from grillmaster.core.tool_session import take_pending_frames
from grillmaster.events.context import current_stage, task_scope
from grillmaster.events.types import (
    ActivityKind,
    AgentActivity,
    AgentSessionFinished,
    AgentSessionStarted,
    SessionOutcome,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from grillmaster.agents.adapters.base import (
        AgentAdapter,
        AgentSession,
        FinalOutput,
        SessionHandle,
    )
    from grillmaster.agents.events import AgentEvent
    from grillmaster.agents.schema import JsonSchema
    from grillmaster.agents.task import AgentJob, AgentTask
    from grillmaster.core.model_spec import Backend, ModelSpec, Role
    from grillmaster.events.bus import EventSink

# The MCP tool server; the runner appends `--session <tools.json>`.
DEFAULT_TOOL_SERVER = (sys.executable, "-m", "grillmaster.agent_tools")
DEFAULT_RETRY_DELAY_S = 30.0
# How often `run_jobs` wakes up to let a pending Ctrl-C through.
_INTERRUPT_POLL_S = 0.1

PROMPT_FILE = "prompt.md"
TOOLS_FILE = "tools.json"
SCHEMA_FILE = "schema.json"
RAW_FILE = "raw.jsonl"
RESULT_FILE = "result.json"
# Frames the tool server saved for the next message (`ToolImageDelivery.NEXT_MESSAGE`).
PENDING_FRAMES_FILE = "frames_pending.txt"
# Turns of one session that may end asking for frames; a model that keeps
# asking is stuck, not still reading.
MAX_FRAME_TURNS = 8

# Set while the current context holds a slot; a nested `run` would wait for
# a slot its own caller holds.
_holding_slot: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "grill_agent_slot", default=False
)


class SessionRecord(BaseModel):
    """`result.json`: how one session (one attempt) ended."""

    task: str
    attempt: int
    session_id: str | None
    backend: str
    model: str
    effort: str
    outcome: SessionOutcome
    repairs: int
    # Turns that delivered requested frames (`ToolImageDelivery.NEXT_MESSAGE`).
    frame_turns: int
    elapsed_s: float
    usage: dict[str, int]
    error: str | None


@dataclass(frozen=True, slots=True)
class _Prepared[T]:
    """A task checked and resolved once, before its first attempt."""

    task: AgentTask[T]
    spec: ModelSpec
    adapter: AgentAdapter
    requires: frozenset[Capability]
    schema: JsonSchema | None
    images: tuple[Path, ...]
    audio: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _SessionFiles:
    """What `_prepare_session_dir` wrote that the turns refer to."""

    message: str
    mcp: McpServer | None
    schema_path: Path | None


@dataclass(slots=True)
class _SessionState:
    session_id: str | None = None
    repairs: int = 0
    frame_turns: int = 0
    # Summed over the session's turns.
    usage: Counter[str] = field(default_factory=Counter)


class AgentRunner:
    def __init__(
        self,
        roles: Mapping[Role, ModelSpec],
        adapters: Callable[[Backend], AgentAdapter],
        *,
        max_concurrent: int,
        timeout_s: float,
        events: EventSink,
        tool_server: Sequence[str] = DEFAULT_TOOL_SERVER,
        retry_delay_s: float = DEFAULT_RETRY_DELAY_S,
        clock: Callable[[], float] = time.monotonic,
        abort: threading.Event = ABORT,
    ) -> None:
        """`abort` is the process-wide abort latch (a test may pass its
        own); the retry delay waits on it."""
        if max_concurrent < 1:
            raise ValueError(f"max_concurrent must be >= 1: {max_concurrent}")
        self._roles = dict(roles)
        self._adapters = adapters
        self._max_concurrent = max_concurrent
        self._slots = threading.BoundedSemaphore(max_concurrent)
        self._timeout_s = timeout_s
        self._events = events
        self._tool_server = tuple(tool_server)
        self._retry_delay_s = retry_delay_s
        self._clock = clock
        self._abort = abort
        # The first quota error of each backend; later tasks fail with it.
        self._exhausted: dict[Backend, AgentQuotaError] = {}
        # Workdirs of in-flight runs: two sessions in one cwd would clobber
        # each other's files (agy's MCP config, `FilesOutput` targets).
        self._active_workdirs: set[Path] = set()
        self._workdirs_lock = threading.Lock()

    def capabilities(self, role: Role) -> frozenset[Capability]:
        """What the backend configured for `role` can do (e.g. hear audio)."""
        spec = self._roles.get(role)
        if spec is None:
            raise AgentConfigError(f"no model configured for role {role}")
        return self._adapters(spec.backend).capabilities

    def run[T](self, task: AgentTask[T]) -> AgentResult[T]:
        """Run `task` to an accepted output or raise the classified `AgentError`."""
        if _holding_slot.get():
            raise AgentConfigError(
                f"{task.name}: nested agent run while holding a slot would deadlock"
            )
        prepared = self._prepare(task)
        backend = prepared.spec.backend
        exhausted = self._exhausted.get(backend)
        if exhausted is not None:
            raise AgentQuotaError(
                f"{task.name}: not started: {backend} hit its quota earlier: "
                f"{exhausted}"
            )
        with ExitStack() as stack:
            if task.workdir is None:
                # A CLI descendant may still hold a file open on Windows;
                # a leftover temp dir must not fail an accepted session.
                workdir = Path(
                    stack.enter_context(
                        tempfile.TemporaryDirectory(
                            prefix="grill_agent_", ignore_cleanup_errors=True
                        )
                    )
                )
            else:
                task.workdir.mkdir(parents=True, exist_ok=True)
                workdir = task.workdir.resolve()
                stack.enter_context(self._claim_workdir(task.name, workdir))
            attempt = 1
            while True:
                try:
                    return self._attempt(prepared, workdir, attempt)
                except AgentQuotaError as error:
                    self._exhausted.setdefault(backend, error)
                    raise
                except AgentTransientError:
                    if attempt >= task.attempts:
                        raise
                    # The slot is already released; waiting must not hold one.
                    # An abort wakes it; the next attempt then refuses to start.
                    self._abort.wait(self._retry_delay_s)
                attempt += 1

    def run_jobs[T](self, jobs: Sequence[AgentJob[T]]) -> list[JobFailure]:
        """Run `jobs` concurrently (bounded by the global slots) and return
        the failures in input order.

        Each job prepares its task, runs it and accepts the result on one
        worker; any exception in those steps fails only that job (after a
        quota error, the queued jobs of that backend fail at once). An
        interrupt while waiting sets the abort latch and cancels the jobs not
        yet started, lets the running ones end (accepting what succeeds; no
        new turn starts), then propagates.
        """
        if _holding_slot.get():
            raise AgentConfigError("nested agent run_jobs while holding a slot")
        if not jobs:
            return []

        def run_one(job: AgentJob[T]) -> JobFailure | None:
            try:
                job.accept(self.run(job.prepare()))
            except (AgentCancelledError, AgentQuotaError) as error:
                logger.error(f"{job.name} failed: {error}")
                return JobFailure(job.name, error)
            except Exception as error:  # noqa: BLE001 - reported per job
                logger.opt(exception=True).debug(f"{job.name} traceback")
                logger.error(f"{job.name} failed: {error}")
                return JobFailure(job.name, error)
            return None

        pool = ThreadPoolExecutor(
            max_workers=min(len(jobs), self._max_concurrent),
            thread_name_prefix="agent",
        )
        try:
            # One context copy per job: a Context cannot be entered by two
            # threads at once.
            futures = [
                pool.submit(contextvars.copy_context().run, run_one, job)
                for job in jobs
            ]
            pending = set(futures)
            while pending:
                # A timed wait: an untimed one would hold Ctrl-C back until
                # a job finished (lock waits are not interruptible on Windows).
                done, pending = wait(
                    pending, timeout=_INTERRUPT_POLL_S, return_when=FIRST_EXCEPTION
                )
                for future in done:
                    # Re-raises what `run_one` lets through (an interrupt).
                    future.result()
            outcomes = [future.result() for future in futures]
        except BaseException:
            # Process-terminal (Ctrl-C): no worker may start a new session
            # or child process while the running jobs wind down.
            self._abort.set()
            pool.shutdown(wait=True, cancel_futures=True)
            raise
        pool.shutdown(wait=True)
        return [failure for failure in outcomes if failure is not None]

    # -- preparation ---------------------------------------------------------

    def _prepare[T](self, task: AgentTask[T]) -> _Prepared[T]:
        """Every configuration check, before any slot or session file."""
        spec = self._roles.get(task.role)
        if spec is None:
            raise AgentConfigError(
                f"{task.name}: no model configured for role {task.role}"
            )
        adapter = self._adapters(spec.backend)
        requires = _required_capabilities(task, adapter.tool_image_delivery)
        missing = sorted(requires - adapter.capabilities)
        if missing:
            raise AgentConfigError(
                f"{task.name}: {spec.backend} cannot provide "
                f"{', '.join(missing)} (role {task.role}, spec {spec})"
            )
        absent = [
            str(path) for path in (*task.images, *task.audio) if not path.is_file()
        ]
        if absent:
            raise AgentConfigError(
                f"{task.name}: input files missing: {', '.join(absent)}"
            )
        schema = None
        if isinstance(task.output, SchemaOutput):
            model: type[BaseModel] = task.output.model
            try:
                schema = strict_json_schema(model)
            except StrictSchemaError as error:
                raise AgentConfigError(f"{task.name}: {error}") from error
        images = tuple(path.resolve() for path in task.images)
        audio = tuple(path.resolve() for path in task.audio)
        adapter.preflight(spec, images, audio)
        return _Prepared(task, spec, adapter, requires, schema, images, audio)

    def _prepare_session_dir(
        self, prepared: _Prepared[Any], session_dir: Path
    ) -> _SessionFiles:
        """A fresh session directory holding the files every turn reuses."""
        task, adapter = prepared.task, prepared.adapter
        tools = task.tools
        if (
            tools is not None
            and tools.frames is not None
            and adapter.tool_image_delivery is ToolImageDelivery.NEXT_MESSAGE
        ):
            pending = session_dir / PENDING_FRAMES_FILE
            frames = tools.frames.model_copy(update={"pending_frames": pending})
            tools = tools.model_copy(update={"frames": frames})
        message = prompt.compose_message(
            task.instructions,
            task.prompt,
            tools=tools,
            images=prepared.images,
            audio=prepared.audio,
            media_delivery=adapter.media_delivery,
            tool_images=adapter.tool_image_delivery,
            schema=prepared.schema,
            schema_delivery=adapter.schema_delivery,
        )
        if session_dir.exists():
            shutil.rmtree(session_dir)
        session_dir.mkdir(parents=True)
        atomic_write_text(session_dir / PROMPT_FILE, message + "\n")
        mcp = None
        if tools is not None:
            manifest = session_dir / TOOLS_FILE
            tools.write(manifest)
            command, *args = self._tool_server
            mcp = McpServer(command, (*args, "--session", str(manifest)))
        schema_path = None
        if prepared.schema is not None:
            schema_path = session_dir / SCHEMA_FILE
            atomic_write_text(schema_path, json.dumps(prepared.schema, indent=2))
        return _SessionFiles(message, mcp, schema_path)

    # -- one attempt = one session ------------------------------------------

    def _attempt[T](
        self, prepared: _Prepared[T], workdir: Path, attempt: int
    ) -> AgentResult[T]:
        task, spec = prepared.task, prepared.spec
        session_dir = _session_dir(task.session_dir, attempt)
        with task_scope(task.name), self._slot():
            # Checked once the slot is ours: waiting for it may outlast an abort.
            if self._abort.is_set():
                raise self._cancelled(task.name, "a session")
            files = self._prepare_session_dir(prepared, session_dir)
            if isinstance(task.output, FilesOutput):
                for path in task.output.declared(workdir):
                    path.unlink(missing_ok=True)
            started = self._clock()
            state = _SessionState()
            self._events.emit(
                AgentSessionStarted(
                    task=task.name,
                    stage=current_stage(),
                    backend=str(spec.backend),
                    model=spec.model,
                    effort=str(spec.effort),
                )
            )
            error: BaseException | None = None
            try:
                with (session_dir / RAW_FILE).open(
                    "a", encoding="utf-8", newline="\n"
                ) as raw:
                    request = self._request(prepared, workdir, session_dir, files, raw)
                    try:
                        with closing(prepared.adapter.session()) as session:
                            output = self._session(
                                prepared, session, request, workdir, state
                            )
                    except ProcessAbortedError as aborted:
                        raise self._cancelled(task.name, "a turn") from aborted
            except BaseException as caught:
                error = caught
                raise
            finally:
                elapsed = self._clock() - started
                self._finish(
                    prepared,
                    session_dir=session_dir,
                    attempt=attempt,
                    state=state,
                    elapsed=elapsed,
                    error=error,
                )
        return AgentResult(
            output=output,
            session_id=state.session_id or "",
            spec=spec,
            repairs=state.repairs,
            attempt=attempt,
            elapsed_s=elapsed,
            session_dir=session_dir,
            usage=dict(state.usage),
        )

    def _session[T](
        self,
        prepared: _Prepared[T],
        session: AgentSession,
        request: TurnRequest,
        workdir: Path,
        state: _SessionState,
    ) -> T:
        """Turns until an accepted output. A turn that asked for frames
        (`ToolImageDelivery.NEXT_MESSAGE`) is answered with them, not judged:
        it was told to end with a note while waiting (which a `TextOutput`
        would accept), and its output was written before the model saw them.
        Otherwise a defect or a failed validation is answered with a repair
        round."""
        task = prepared.task
        pending_frames = request.session_dir / PENDING_FRAMES_FILE
        handle = session.start(request)
        while True:
            final = self._consume(task.name, handle, audio=bool(prepared.audio))
            state.session_id = final.session_id or state.session_id
            state.usage.update(final.usage)
            if frames := take_pending_frames(pending_frames):
                if state.frame_turns >= MAX_FRAME_TURNS:
                    raise AgentOutputError(
                        f"{task.name}: still asking for frames after "
                        f"{state.frame_turns} frame turn(s)"
                    )
                self._check_resumable(task.name, final, "a frames turn")
                state.frame_turns += 1
                summary = f"get_frames: {len(frames)} frame(s) attached"
                self._activity(task.name, ActivityKind.TOOL_RESULT, summary)
                message = prompt.frames_message()
                turn = replace(request, message=message, images=frames, audio=())
                handle = session.resume(final.session_id, turn)
                continue
            # The adapter's own findings come first: the output of a turn
            # that skipped its inputs is not worth judging.
            defect = _merge_defects(final.defects) if final.defects else None
            if defect is None:
                try:
                    return self._accept(prepared, final, workdir)
                except ValidationFailure as failure:
                    defect = TurnDefect(str(failure))
            if state.repairs >= task.max_repairs:
                raise AgentOutputError(
                    f"{task.name}: output still invalid after "
                    f"{state.repairs} repair round(s): {defect.message}"
                )
            self._check_resumable(task.name, final, "a repair")
            state.repairs += 1
            self._activity(task.name, ActivityKind.REPAIR, first_line(defect.message))
            handle = session.resume(
                final.session_id,
                replace(
                    request,
                    message=prompt.repair_message(defect.message),
                    images=defect.images,
                    audio=defect.audio,
                ),
            )

    def _check_resumable(self, task_name: str, final: FinalOutput, step: str) -> None:
        """Raise unless the session can take `step`, another turn."""
        if not final.session_id:
            raise AgentTransientError(
                f"{task_name}: no session id to resume for {step}"
            )
        if self._abort.is_set():
            raise self._cancelled(task_name, step)

    def _accept[T](
        self, prepared: _Prepared[T], final: FinalOutput, workdir: Path
    ) -> T:
        task = prepared.task
        if (
            prepared.schema is not None
            and prepared.adapter.schema_delivery is SchemaDelivery.PROMPT
        ):
            # The other half of `prompt.answer_schema_section`. A model may
            # write prose or leaked reasoning before the closing json block;
            # a repair round for that would resend the whole session.
            answer = json_object_answer(final.text, lead_in=True)
            final = replace(final, structured=answer)
        output = task.output.parse(final, workdir)
        if task.validate is not None:
            task.validate(output)
        return output

    def _consume(
        self, task_name: str, handle: SessionHandle, *, audio: bool
    ) -> FinalOutput:
        """Emit the turn's activity; the newest message is held back because
        the turn's last one is its final output, summarized by length.

        With `audio`, a message carrying the audio-unavailable marker stops
        the turn at once (the CLI is ended) and raises `AgentInputError`."""
        held: Message | None = None
        with closing(handle.events()) as events:
            for event in events:
                if held is not None:
                    self._emit_event(task_name, held)
                    held = None
                if audio and isinstance(event, Message):
                    self._refuse_unheard_audio(task_name, event)
                if isinstance(event, Message):
                    held = event
                else:
                    self._emit_event(task_name, event)
        final = handle.result()
        if final.text:
            self._activity(task_name, *summarize_final(final.text))
        return final

    def _refuse_unheard_audio(self, task_name: str, message: Message) -> None:
        """Raise `AgentInputError` if `message` says the audio was not heard."""
        reason = prompt.audio_unavailable(message.text)
        if reason is None:
            return
        self._emit_event(task_name, message)
        raise AgentInputError(
            f"{task_name}: the model cannot hear its audio: "
            f"{reason or '(no reason given)'}"
        )

    # -- plumbing -------------------------------------------------------------

    def _cancelled(self, task_name: str, step: str) -> AgentCancelledError:
        return AgentCancelledError(f"{task_name}: the run was aborted before {step}")

    def _request(
        self,
        prepared: _Prepared[Any],
        workdir: Path,
        session_dir: Path,
        files: _SessionFiles,
        raw: TextIO,
    ) -> TurnRequest:
        def write_raw(line: str) -> None:
            raw.write(line + "\n")

        return TurnRequest(
            message=files.message,
            spec=prepared.spec,
            workdir=workdir,
            session_dir=session_dir,
            timeout_s=self._timeout_s,
            raw=write_raw,
            schema=prepared.schema,
            schema_path=files.schema_path,
            images=prepared.images,
            audio=prepared.audio,
            add_dirs=tuple(path.resolve() for path in prepared.task.add_dirs),
            mcp=files.mcp,
            web_search=Capability.WEB_SEARCH in prepared.requires,
        )

    @contextmanager
    def _claim_workdir(self, task_name: str, workdir: Path) -> Iterator[None]:
        with self._workdirs_lock:
            if workdir in self._active_workdirs:
                raise AgentConfigError(
                    f"{task_name}: workdir {workdir} is in use by another "
                    "running agent task"
                )
            self._active_workdirs.add(workdir)
        try:
            yield
        finally:
            with self._workdirs_lock:
                self._active_workdirs.discard(workdir)

    @contextmanager
    def _slot(self) -> Iterator[None]:
        with self._slots:
            token = _holding_slot.set(True)
            try:
                yield
            finally:
                _holding_slot.reset(token)

    def _finish(
        self,
        prepared: _Prepared[Any],
        *,
        session_dir: Path,
        attempt: int,
        state: _SessionState,
        elapsed: float,
        error: BaseException | None,
    ) -> None:
        # Unclassified exceptions are bugs; they still end the session for
        # the TUI and propagate unchanged.
        outcome = (
            SessionOutcome.OK
            if error is None
            else error.outcome
            if isinstance(error, AgentError)
            else SessionOutcome.TRANSIENT_ERROR
        )
        usage = dict(state.usage)
        spec = prepared.spec
        write_model(
            session_dir / RESULT_FILE,
            SessionRecord(
                task=prepared.task.name,
                attempt=attempt,
                session_id=state.session_id,
                backend=str(spec.backend),
                model=spec.model,
                effort=str(spec.effort),
                outcome=outcome,
                repairs=state.repairs,
                frame_turns=state.frame_turns,
                elapsed_s=round(elapsed, 3),
                usage=usage,
                error=None if error is None else f"{type(error).__name__}: {error}",
            ),
        )
        self._events.emit(
            AgentSessionFinished(
                task=prepared.task.name,
                outcome=outcome,
                elapsed=elapsed,
                repairs=state.repairs,
                usage=usage,
            )
        )

    def _emit_event(self, task_name: str, event: AgentEvent) -> None:
        summary = summarize(event)
        if summary is not None:
            self._activity(task_name, *summary)

    def _activity(self, task_name: str, kind: ActivityKind, summary: str) -> None:
        self._events.emit(AgentActivity(task=task_name, kind=kind, summary=summary))


def _required_capabilities(
    task: AgentTask[Any], tool_images: ToolImageDelivery
) -> frozenset[Capability]:
    """What the task declares plus what its inputs and output imply, and,
    for frames attached to the next message, a resumed turn with images."""
    required = set(task.requires)
    if isinstance(task.output, SchemaOutput):
        required.add(Capability.SCHEMA_OUTPUT)
    if task.max_repairs > 0:
        required.add(Capability.RESUME)
    if task.images:
        required.add(Capability.IMAGE_INPUT)
    if task.audio:
        required.add(Capability.AUDIO_INPUT)
    if task.tools is not None:
        required.add(Capability.MCP)
        if (
            task.tools.frames is not None
            and tool_images is ToolImageDelivery.NEXT_MESSAGE
        ):
            required |= {Capability.RESUME, Capability.IMAGE_INPUT}
    return frozenset(required)


def _merge_defects(defects: Sequence[TurnDefect]) -> TurnDefect:
    """One repair round answers every defect of the turn."""
    return TurnDefect(
        message="\n\n".join(defect.message for defect in defects),
        images=tuple(dict.fromkeys(p for defect in defects for p in defect.images)),
        audio=tuple(dict.fromkeys(p for defect in defects for p in defect.audio)),
    )


def _session_dir(base: Path, attempt: int) -> Path:
    return attempt_path(base.resolve(), attempt)
