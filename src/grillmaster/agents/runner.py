"""`AgentRunner`: the only way the pipeline talks to an agent.

Per task: resolve the role's `ModelSpec` and adapter, refuse what the adapter
cannot do (capabilities, then the adapter's own `preflight`), then run
attempts. Each attempt is one fresh session holding one slot of the global
concurrency limit from its first turn through every repair round; repair
rounds resume that session with only the repair message. Only
`AgentTransientError` starts another attempt, after a delay spent without a
slot. Every session leaves `prompt.md`, `tools.json`, `schema.json`,
`raw.jsonl` and `result.json` in its session directory.
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
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from loguru import logger
from pydantic import BaseModel

from grillmaster.agents import prompt
from grillmaster.agents.adapters.base import (
    Capability,
    McpServer,
    MediaDelivery,
    TurnDefect,
    TurnRequest,
)
from grillmaster.agents.errors import (
    AgentConfigError,
    AgentError,
    AgentOutputError,
    AgentTransientError,
    ValidationFailure,
)
from grillmaster.agents.events import (
    Message,
    first_line,
    summarize,
    summarize_final,
)
from grillmaster.agents.schema import StrictSchemaError, strict_json_schema
from grillmaster.agents.task import (
    AgentResult,
    FilesOutput,
    JobFailure,
    SchemaOutput,
)
from grillmaster.core.fs import atomic_write_text
from grillmaster.core.json_artifact import write_model
from grillmaster.core.paths import attempt_path
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
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
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
        self._sleep = sleep
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
        with ExitStack() as stack:
            if task.workdir is None:
                workdir = Path(
                    stack.enter_context(
                        tempfile.TemporaryDirectory(prefix="grill_agent_")
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
                except AgentTransientError:
                    if attempt >= task.attempts:
                        raise
                # The slot is already released; waiting must not hold one.
                self._sleep(self._retry_delay_s)
                attempt += 1

    def run_jobs[T](self, jobs: Sequence[AgentJob[T]]) -> list[JobFailure]:
        """Run `jobs` concurrently (bounded by the global slots) and return
        the failures in input order.

        Each job prepares its task, runs it and accepts the result on one
        worker; any exception in those steps fails only that job. An
        interrupt while waiting cancels the jobs not yet started, lets the
        running ones finish (accepting what succeeds), then propagates.
        """
        if _holding_slot.get():
            raise AgentConfigError("nested agent run_jobs while holding a slot")
        if not jobs:
            return []

        def run_one(job: AgentJob[T]) -> JobFailure | None:
            try:
                job.accept(self.run(job.prepare()))
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
        requires = _required_capabilities(task)
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
            model: type[BaseModel] = task.output.model  # pyright: ignore[reportUnknownMemberType]
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
        task = prepared.task
        view_file = prepared.adapter.media_delivery is MediaDelivery.VIEW_FILE
        message = prompt.compose_message(
            task.instructions,
            task.prompt,
            tools=task.tools,
            view_file_images=prepared.images if view_file else (),
            view_file_audio=prepared.audio if view_file else (),
        )
        if session_dir.exists():
            shutil.rmtree(session_dir)
        session_dir.mkdir(parents=True)
        atomic_write_text(session_dir / PROMPT_FILE, message + "\n")
        mcp = None
        if task.tools is not None:
            manifest = session_dir / TOOLS_FILE
            task.tools.write(manifest)
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
        files = self._prepare_session_dir(prepared, session_dir)
        if isinstance(task.output, FilesOutput):
            for path in task.output.declared(workdir):
                path.unlink(missing_ok=True)

        with task_scope(task.name), self._slot():
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
                    output = self._session(prepared, request, workdir, state)
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
        request: TurnRequest,
        workdir: Path,
        state: _SessionState,
    ) -> T:
        task, adapter = prepared.task, prepared.adapter
        handle = adapter.start(request)
        while True:
            final = self._consume(task.name, handle)
            state.session_id = final.session_id or state.session_id
            state.usage.update(final.usage)
            # The adapter's own findings come first: the output of a turn
            # that skipped its inputs is not worth judging.
            defect = _merge_defects(final.defects) if final.defects else None
            if defect is None:
                try:
                    return self._accept(task, final, workdir)
                except ValidationFailure as failure:
                    defect = TurnDefect(str(failure))
            if state.repairs >= task.max_repairs:
                raise AgentOutputError(
                    f"{task.name}: output still invalid after "
                    f"{state.repairs} repair round(s): {defect.message}"
                )
            if not final.session_id:
                raise AgentTransientError(
                    f"{task.name}: no session id to resume for a repair"
                )
            state.repairs += 1
            self._activity(task.name, ActivityKind.REPAIR, first_line(defect.message))
            handle = adapter.resume(
                final.session_id,
                replace(
                    request,
                    message=prompt.repair_message(defect.message),
                    images=defect.images,
                    audio=defect.audio,
                ),
            )

    def _accept[T](self, task: AgentTask[T], final: FinalOutput, workdir: Path) -> T:
        output = task.output.parse(final, workdir)
        if task.validate is not None:
            task.validate(output)
        return output

    def _consume(self, task_name: str, handle: SessionHandle) -> FinalOutput:
        """Emit the turn's activity; the newest message is held back because
        the turn's last one is its final output, summarized by length."""
        held: Message | None = None
        for event in handle.events():
            if held is not None:
                self._emit_event(task_name, held)
                held = None
            if isinstance(event, Message):
                held = event
            else:
                self._emit_event(task_name, event)
        final = handle.result()
        if final.text:
            self._activity(task_name, *summarize_final(final.text))
        return final

    # -- plumbing -------------------------------------------------------------

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


def _required_capabilities(task: AgentTask[Any]) -> frozenset[Capability]:
    """What the task declares plus what its inputs and output imply."""
    required = set(task.requires)
    if isinstance(task.output, SchemaOutput):
        required.add(Capability.NATIVE_SCHEMA)
    if task.max_repairs > 0:
        required.add(Capability.RESUME)
    if task.images:
        required.add(Capability.IMAGE_INPUT)
    if task.audio:
        required.add(Capability.AUDIO_INPUT)
    if task.tools is not None:
        required.add(Capability.MCP)
        if task.tools.frames is not None:
            required.add(Capability.MCP_IMAGE_RESULT)
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
