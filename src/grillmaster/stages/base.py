"""The stage API: what a stage, side task or delivery step is (`StageDef`,
`SideTaskDef`, `DeliveryStepDef`), what a run asks for (`RunOptions`), and
what each one runs with (`StageContext`).

A stage module under `stages/` exports one definition; `pipeline.registry`
lists them and the pipeline runs them. Everything here sits below
`pipeline`, so stage modules never import upward.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from grillmaster.live_chat.layout import DEFAULT_CHAT_LAYOUT, ChatLayout
from grillmaster.project.layout import session_dir
from grillmaster.project.state import Section, TaskRecord, now

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from grillmaster.agents.runner import AgentRunner
    from grillmaster.asr.client import SpeechToTextFactory
    from grillmaster.config.load import LoadedConfig
    from grillmaster.config.model import AppConfig
    from grillmaster.config.secrets import Secrets
    from grillmaster.core.source_id import SourceId
    from grillmaster.core.stage_key import SideTaskKey, StageKey
    from grillmaster.events.bus import EventSink
    from grillmaster.events.types import SkipReason
    from grillmaster.media.ffmpeg import FfmpegRunner
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState
    from grillmaster.sources.http import JsonHttp
    from grillmaster.sources.ytdlp import YtDlp


@dataclass(frozen=True, slots=True)
class RunOptions:
    """Everything one `grill run` invocation asks for.

    `cover` / `date_research` / `proofread` / `telop` are the per-run flags;
    their steps combine them with `[features]`. `burn_telops = False`
    (`grill package --no-telop`) leaves translated telops out of the burn-in. `remix` is a resolved pool name (a bare `--remix`
    already became `package.remix_pool`). `section` is where `--start` /
    `--to` cut the source; it only applies while the combine stage has not
    run.
    """

    source: SourceId
    hint: str | None = None
    parent: Path | None = None
    break_after: StageKey | None = None
    cover: bool = False
    date_research: bool = False
    proofread: bool = False
    telop: bool = False
    chat: bool = False
    chat_layout: ChatLayout = DEFAULT_CHAT_LAYOUT
    burn_telops: bool = True
    remix: str | None = None
    section: Section = field(default_factory=Section)

    @property
    def has_section(self) -> bool:
        return self.section.is_cut

    @property
    def complete_run(self) -> bool:
        """No `--break-after`: side tasks and delivery may run."""
        return self.break_after is None


@dataclass(frozen=True, slots=True)
class StepOutcome[T]:
    """A finished step's return value, elapsed time and agent usage."""

    value: T
    elapsed: float
    usage: Mapping[str, int] | None


@dataclass(frozen=True, slots=True)
class Externals:
    """The processes and services stages reach, built once per run; tests
    hand in fakes."""

    ffmpeg: FfmpegRunner
    ytdlp: YtDlp
    http: JsonHttp
    speech_to_text: SpeechToTextFactory


def always(_options: RunOptions, _config: AppConfig) -> bool:
    """`StageDef.enabled` / `DeliveryStepDef.enabled` default: every run."""
    return True


def no_outputs(_layout: ProjectLayout) -> Sequence[Path]:
    """`StageDef.outputs` default: nothing outside the stage's workdir."""
    return ()


def no_params(_config: AppConfig) -> dict[str, str]:
    """`params` default: nothing worth showing."""
    return {}


def no_clear(_state: ProjectState) -> None:
    """`StageDef.clear_state` default: the stage writes no state fields."""


def no_preflight(_config: AppConfig, _secrets: Secrets) -> None:
    """`StageDef.preflight` default: nothing to check before the run."""


def no_delivery_preflight(
    _options: RunOptions, _config: AppConfig, _state: ProjectState | None
) -> None:
    """`DeliveryStepDef.preflight` default: nothing to check before the run."""


def no_reset(_layout: ProjectLayout) -> None:
    """`StageDef.on_reset` default: removing the stage's files is enough."""


def describe(value: object) -> str | None:
    """`SideTaskDef.describe` default: the payload as text, if any."""
    return None if value is None else str(value)


class MissingArtifactError(Exception):
    """An upstream artifact a stage reads is gone; names the reset that
    produces it again."""

    def __init__(self, path: Path, produced_by: StageKey) -> None:
        super().__init__(
            f"{path} is missing; run `grill reset <id> --from {produced_by}` "
            "to produce it again"
        )
        self.path = path
        self.produced_by = produced_by


def require(path: Path, produced_by: StageKey) -> Path:
    """`path`, which stage `produced_by` writes; `MissingArtifactError`
    when it does not exist."""
    if not path.exists():
        raise MissingArtifactError(path, produced_by)
    return path


@dataclass(frozen=True, slots=True)
class StageDef:
    """One resumable pipeline stage.

    `run` does the work and returns normally on success, optionally with
    the `StepCompleted` result text; the runner then records the stage in
    the ledger. `outputs` lists the root deliverables the stage writes
    besides its `.work/NN_<stage>/` directory (what `grill reset` deletes);
    `clear_state` resets the `ProjectState` fields it writes (name,
    broadcast date, section, ...) on the same reset. `on_skip` runs when a
    resume skips the stage as already complete (e.g. warn that `--start` /
    `--to` no longer apply). `params` is the display snapshot shown in the
    plan and stored in the ledger, never a cache key. `preflight` raises
    when the configuration cannot carry the stage (a missing API key);
    `Pipeline.check` runs it before any stage does work. `on_reset` runs
    when `grill reset` clears the stage, before any file is deleted, for
    an output that must go back upstream rather than be deleted.
    `applies`, checked when the stage's turn comes and the project's
    program is known, can still skip an enabled stage as disabled
    (per-program switches), like `SideTaskDef.applies`.
    """

    key: StageKey
    label: str
    weight: int
    run: Callable[[StageContext], str | None]
    outputs: Callable[[ProjectLayout], Sequence[Path]] = no_outputs
    enabled: Callable[[RunOptions, AppConfig], bool] = always
    applies: Callable[[StageContext], bool] | None = None
    on_skip: Callable[[StageContext], None] | None = None
    params: Callable[[AppConfig], dict[str, str]] = no_params
    clear_state: Callable[[ProjectState], None] = no_clear
    preflight: Callable[[AppConfig, Secrets], None] = no_preflight
    on_reset: Callable[[ProjectLayout], None] = no_reset

    def __post_init__(self) -> None:
        if self.weight < 1:
            raise ValueError(f"Stage {self.key} weight must be >= 1: {self.weight}")


@dataclass(frozen=True, slots=True)
class SideTaskDef[T]:
    """One side task.

    `run` does the work on a worker thread and returns only its payload;
    the manager then writes the task's record into `state.side_tasks` under
    the state lock: `record(state, outcome)` when given (a task-specific
    record shape), else a plain `TaskRecord` of the step's elapsed time and
    agent usage. `describe` turns the payload into the `StepCompleted`
    result text. `enabled` combines the run flag with `[features]`;
    `applies`, checked at the start point where the project's program is
    known, can still skip it as disabled (per-program switches). `is_done`
    overrides the default completion check
    (`state.side_tasks.is_done(key)`), e.g. date research is moot once the
    metadata found a date. `on_skip` runs on the pipeline thread whenever
    the task is not started at its start point, with the reason.
    """

    key: SideTaskKey
    label: str
    weight: int
    start_after: StageKey
    run: Callable[[StageContext], T]
    enabled: Callable[[RunOptions, AppConfig], bool]
    applies: Callable[[StageContext], bool] | None = None
    record: Callable[[ProjectState, StepOutcome[T]], None] | None = None
    describe: Callable[[T], str | None] = describe
    is_done: Callable[[ProjectState], bool] | None = None
    on_skip: Callable[[StageContext, SkipReason], None] | None = None
    params: Callable[[AppConfig], dict[str, str]] = no_params

    def done(self, state: ProjectState) -> bool:
        if self.is_done is not None:
            return self.is_done(state)
        return state.side_tasks.is_done(self.key)

    def store(self, state: ProjectState, outcome: StepOutcome[T]) -> None:
        """Write the record of a finished run (see `record`)."""
        if self.record is not None:
            self.record(state, outcome)
            return
        state.side_tasks.record(
            self.key,
            TaskRecord(
                completed_at=now(),
                elapsed_s=outcome.elapsed,
                agent_usage=outcome.usage,
            ),
        )


@dataclass(frozen=True, slots=True)
class DeliveryStepDef:
    """One delivery step.

    `run` returns the `StepCompleted` result text; `workdir` is the step's
    own directory (its `StageContext.workdir`). `preflight` raises when the
    step would fail on its configuration (a missing pool); `Pipeline.check`
    runs it before any stage when the step will run, with the project's
    state when it exists (`None` before the first run).
    """

    key: str
    label: str
    weight: int
    run: Callable[[StageContext], str | None]
    workdir: Callable[[ProjectLayout], Path]
    enabled: Callable[[RunOptions, AppConfig], bool] = always
    params: Callable[[AppConfig], dict[str, str]] = no_params
    preflight: Callable[[RunOptions, AppConfig, ProjectState | None], None] = (
        no_delivery_preflight
    )


class StateAccess(Protocol):
    """The run's state and its locked, atomic persistence (the pipeline's
    `StateStore`)."""

    @property
    def state(self) -> ProjectState: ...

    def save(self) -> None: ...

    def update(self, change: Callable[[ProjectState], None]) -> None: ...


class SideTaskWaiter(Protocol):
    """Waits for a side task of the run (the pipeline's `SideTaskManager`)."""

    def wait(self, key: SideTaskKey) -> None: ...


class _NoSideTasks:
    """The waiter of a step that runs beside no side task."""

    def wait(self, key: SideTaskKey) -> None:
        pass


NO_SIDE_TASKS: SideTaskWaiter = _NoSideTasks()


class StageContext:
    """What a stage, side task or delivery step runs with.

    A stage hands the config sections its domain module needs on as
    explicit inputs. `workdir` is the step's own directory
    (`.work/NN_<stage>/`, `.work/side/<task>/` or the delivery step's declared
    one), created on first access so stages without intermediates leave no
    empty directory. `ffmpeg`, `ytdlp`, `http` and `speech_to_text` are the
    run's `Externals`. `side_tasks.wait(key)` blocks until that side task,
    if this run started it, has finished (a stage reading its output); it
    returns at once otherwise.
    """

    __slots__ = (
        "_loaded",
        "_store",
        "_workdir",
        "_workdir_ready",
        "agents",
        "events",
        "ffmpeg",
        "http",
        "layout",
        "options",
        "side_tasks",
        "speech_to_text",
        "ytdlp",
    )

    def __init__(
        self,
        *,
        layout: ProjectLayout,
        store: StateAccess,
        loaded: LoadedConfig,
        options: RunOptions,
        agents: AgentRunner,
        events: EventSink,
        externals: Externals,
        workdir: Path,
        side_tasks: SideTaskWaiter = NO_SIDE_TASKS,
    ) -> None:
        self.layout = layout
        self.options = options
        self.agents = agents
        self.events = events
        self.ffmpeg = externals.ffmpeg
        self.ytdlp = externals.ytdlp
        self.http = externals.http
        self.speech_to_text = externals.speech_to_text
        self.side_tasks = side_tasks
        self._store = store
        self._loaded = loaded
        self._workdir = workdir
        self._workdir_ready = False

    @property
    def state(self) -> ProjectState:
        return self._store.state

    def save(self) -> None:
        """Write `project.json` atomically."""
        self._store.save()

    def update(self, change: Callable[[ProjectState], None]) -> None:
        """Mutate the state and save it atomically, under the lock side
        tasks share; use it for dict- or list-valued fields."""
        self._store.update(change)

    @property
    def config(self) -> AppConfig:
        return self._loaded.config

    @property
    def secrets(self) -> Secrets:
        return self._loaded.secrets

    @property
    def env_file(self) -> Path:
        """The `.env` the secrets come from, for a tool server to read again."""
        return self._loaded.env_file

    @property
    def config_file(self) -> Path:
        """`grill.toml`; only the download stage writes it (new programs)."""
        return self._loaded.path

    @property
    def workdir(self) -> Path:
        if not self._workdir_ready:
            self._workdir.mkdir(parents=True, exist_ok=True)
            self._workdir_ready = True
        return self._workdir

    def session_dir(self, label: str = "") -> Path:
        """An agent session record directory in `workdir`; `label` tells
        apart several tasks of one step (`project.layout.session_dir`)."""
        return session_dir(self.workdir, label=label)
