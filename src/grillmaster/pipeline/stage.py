"""What a stage is (`StageDef`), what a run asks for (`RunOptions`), and what a
stage gets to work with (`StageContext`).

A stage module under `stages/` exports one `StageDef`; `pipeline.registry`
lists them in `StageKey` order. The runner, `grill reset`, the TUI plan and
the ledger all read the same definition, so adding a stage touches one place.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from grillmaster.live_chat.layout import DEFAULT_CHAT_LAYOUT, ChatLayout
from grillmaster.project.state import Section
from grillmaster.project.store import save_state

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from grillmaster.agents.runner import AgentRunner
    from grillmaster.config.load import LoadedConfig
    from grillmaster.config.model import AppConfig
    from grillmaster.config.secrets import Secrets
    from grillmaster.core.source_id import SourceId
    from grillmaster.core.stage_key import StageKey
    from grillmaster.events.bus import EventSink
    from grillmaster.project.layout import ProjectLayout
    from grillmaster.project.state import ProjectState


@dataclass(frozen=True, slots=True)
class RunOptions:
    """Everything one `grill run` invocation asks for.

    `cover` / `date_research` are the per-run flags; the side tasks combine
    them with `[features]`. `remix` is a resolved pool name (a bare `--remix`
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
    chat: bool = False
    chat_layout: ChatLayout = DEFAULT_CHAT_LAYOUT
    remix: str | None = None
    section: Section = field(default_factory=Section)

    @property
    def has_section(self) -> bool:
        return self.section.is_cut

    @property
    def complete_run(self) -> bool:
        """No `--break-after`: side tasks and delivery may run."""
        return self.break_after is None


def always(_options: RunOptions) -> bool:
    """`StageDef.enabled` default: the stage runs on every run."""
    return True


def no_params(_config: AppConfig) -> dict[str, str]:
    """`StageDef.params` default: nothing worth showing."""
    return {}


def no_clear(_state: ProjectState) -> None:
    """`StageDef.clear_state` default: the stage writes no state fields."""


@dataclass(frozen=True, slots=True)
class StageDef:
    """One resumable pipeline stage.

    `run` does the work and returns normally on success; the runner then
    records the stage in the ledger. `outputs` lists the root deliverables
    the stage writes besides its `work/NN_<stage>/` directory (what `grill
    reset` deletes); `clear_state` resets the `ProjectState` fields it
    writes (name, broadcast date, section, ...) on the same reset. `on_skip`
    runs when a resume skips the stage as already complete (e.g. warn that
    `--start` / `--to` no longer apply). `params` is the display snapshot
    shown in the plan and stored in the ledger, never a cache key.
    """

    key: StageKey
    label: str
    weight: int
    run: Callable[[StageContext], None]
    outputs: Callable[[ProjectLayout], Sequence[Path]]
    enabled: Callable[[RunOptions], bool] = always
    on_skip: Callable[[StageContext], None] | None = None
    params: Callable[[AppConfig], dict[str, str]] = no_params
    clear_state: Callable[[ProjectState], None] = no_clear

    def __post_init__(self) -> None:
        if self.weight < 1:
            raise ValueError(f"Stage {self.key} weight must be >= 1: {self.weight}")


class StateStore:
    """The run's `ProjectState` and its atomic, thread-safe persistence.

    Side tasks run beside the stages and write the same `project.json`, so
    every write goes through one lock. Mutate dict- or list-valued fields
    through `update` while side tasks may be running; plain field
    assignments followed by `save` are fine.
    """

    def __init__(self, layout: ProjectLayout, state: ProjectState) -> None:
        self._lock = threading.RLock()
        self._layout = layout
        self._state = state

    @property
    def layout(self) -> ProjectLayout:
        return self._layout

    @property
    def state(self) -> ProjectState:
        return self._state

    def save(self) -> None:
        with self._lock:
            save_state(self._layout, self._state)

    def update(self, change: Callable[[ProjectState], None]) -> None:
        """Apply `change` and save, both under the lock."""
        with self._lock:
            change(self._state)
            save_state(self._layout, self._state)


class StageContext:
    """What a stage, side task or delivery step runs with.

    Only `cli` and `pipeline` read `AppConfig`; a stage hands the sections
    its domain module needs on as explicit inputs. `workdir` is the step's
    own directory (`work/NN_<stage>/`, `work/side/<task>/` or the delivery
    step's declared one), created on first access so stages without
    intermediates leave no empty directory.
    """

    __slots__ = (
        "_loaded",
        "_store",
        "_workdir",
        "_workdir_ready",
        "agents",
        "events",
        "layout",
        "options",
    )

    def __init__(
        self,
        *,
        layout: ProjectLayout,
        store: StateStore,
        loaded: LoadedConfig,
        options: RunOptions,
        agents: AgentRunner,
        events: EventSink,
        workdir: Path,
    ) -> None:
        self.layout = layout
        self.options = options
        self.agents = agents
        self.events = events
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
        """Mutate the state and save it atomically (see `StateStore`)."""
        self._store.update(change)

    @property
    def config(self) -> AppConfig:
        return self._loaded.config

    @property
    def secrets(self) -> Secrets:
        return self._loaded.secrets

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
