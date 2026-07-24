"""Standalone Textual UI prototype for a GrillMaster pipeline dashboard.

This file does NOT import anything from the project. It simulates a full
pipeline run with fake timings so the layout / information design can be
evaluated before wiring it to the real workflow.

Run:
    uv run --with textual --with textual-image python scripts/tui_prototype.py

(`textual-image` is optional: with it, the finished cover artifact is shown
as a real image — Sixel on Windows Terminal 1.22+, auto-fallback to unicode
cells elsewhere. Without it a built-in half-block preview is used.)

Keys: ↑/↓ (or j/k) select stage · f follow running stage · p pause ·
      r restart · q quit

The bottom log pane shows only the selected stage's log; side tasks
(date research, cover generation) run concurrently and are selectable
like any pipeline stage.
"""

from __future__ import annotations

import random
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, RichLog, Static

TICK = 0.1


class State(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"


STATE_ICON = {
    State.PENDING: ("○", "grey58"),
    State.RUNNING: ("▶", "bold yellow1"),
    State.DONE: ("✔", "green3"),
    State.SKIPPED: ("⊘", "grey42"),
}


@dataclass
class Stage:
    key: str
    label: str
    duration: float  # simulated seconds; ignored for the chunk stage
    weight: float  # share of the total-progress bar
    params: dict[str, str]
    # (progress threshold, log line) pairs emitted as the stage advances
    script: list[tuple[float, str]] = field(default_factory=list)
    skipped: bool = False
    state: State = State.PENDING
    progress: float = 0.0  # 0..1
    elapsed: float = 0.0
    script_emitted: int = 0
    result: str = ""


@dataclass
class SideTask:
    key: str
    label: str
    start_after: str  # stage key whose completion triggers this task
    duration: float
    params: dict[str, str]
    script: list[tuple[float, str]] = field(default_factory=list)
    state: State = State.PENDING
    progress: float = 0.0
    elapsed: float = 0.0
    script_emitted: int = 0
    result: str = ""


class ChunkState(Enum):
    PENDING = "pending"
    ACTIVE = "active"
    RETRY = "retry"
    DONE = "done"


CHUNK_STYLE = {
    ChunkState.PENDING: "grey58 on grey19",
    ChunkState.ACTIVE: "black on yellow1",
    ChunkState.RETRY: "black on dark_orange",
    ChunkState.DONE: "black on green3",
}


@dataclass
class Chunk:
    index: int
    lines: tuple[int, int]
    need: float
    done_work: float = 0.0
    state: ChunkState = ChunkState.PENDING
    retries: int = 0
    cost: float = 0.0

    @property
    def progress(self) -> float:
        return min(1.0, self.done_work / self.need)


@dataclass
class ChunkSim:
    """Concurrent chunk-translation simulation (fixed seed for a stable demo)."""

    total: int = 18
    concurrency: int = 4
    chunks: list[Chunk] = field(default_factory=list)
    retries: int = 0
    cost: float = 0.0

    def __post_init__(self) -> None:
        rng = random.Random(7)
        start = 1
        for i in range(self.total):
            span = rng.randint(38, 64)
            self.chunks.append(
                Chunk(i, (start, start + span - 1), need=rng.uniform(2.2, 5.5))
            )
            start += span
        self._rng = rng

    @property
    def done(self) -> int:
        return sum(1 for c in self.chunks if c.state is ChunkState.DONE)

    @property
    def finished(self) -> bool:
        return self.done == self.total

    def tick(self, dt: float, log) -> None:
        active = [
            c for c in self.chunks if c.state in (ChunkState.ACTIVE, ChunkState.RETRY)
        ]
        # Fill worker slots in submission order, like the real thread pool.
        for c in self.chunks:
            if len(active) >= self.concurrency:
                break
            if c.state is ChunkState.PENDING:
                c.state = ChunkState.ACTIVE
                active.append(c)
                log(
                    f"[cyan]chunk[/cyan] #{c.index + 1:02d} started "
                    f"(lines {c.lines[0]}–{c.lines[1]})"
                )
        for c in active:
            c.done_work += dt * self._rng.uniform(0.7, 1.3)
            if c.done_work < c.need:
                continue
            # Chunk finished a model round: occasionally fail validation once.
            if c.retries == 0 and self._rng.random() < 0.22:
                c.retries += 1
                self.retries += 1
                c.state = ChunkState.RETRY
                c.done_work = 0.0
                c.need *= 0.6
                log(
                    f"[dark_orange]chunk[/dark_orange] #{c.index + 1:02d} "
                    f"validation failed → structural fix retry"
                )
            else:
                c.state = ChunkState.DONE
                c.cost = self._rng.uniform(0.012, 0.035)
                self.cost += c.cost
                log(
                    f"[green]chunk[/green] #{c.index + 1:02d} done "
                    f"(retries={c.retries}, ${c.cost:.4f})"
                )


PRE_PASS_STEPS = [
    "Upload audio to model",
    "Identify show & cast",
    "Build glossary candidates",
    "Segment synopsis",
    "Write pre_pass.json",
]


def make_stages() -> list[Stage]:
    return [
        Stage(
            "metadata", "Fetch metadata", 1.2, 1,
            {"platform": "fod", "hint": "auto"},
            script=[(0.5, "resolved fod episode 5d1810 → ザ・細かすぎて伝わらないモノマネ")],
        ),
        Stage(
            "download", "Download video", 5.0, 4,
            {"tool": "yt-dlp", "format": "bv*+ba", "segments": "3"},
            script=[
                (0.05, "segment 1/3 started"),
                (0.34, "segment 1/3 done (512.4 MiB)"),
                (0.36, "segment 2/3 started"),
                (0.67, "segment 2/3 done (498.1 MiB)"),
                (0.69, "segment 3/3 started"),
                (0.99, "segment 3/3 done (503.9 MiB)"),
            ],
        ),
        Stage(
            "combine", "Combine segments", 2.0, 2,
            {"tool": "ffmpeg", "codec": "copy"},
            script=[(0.2, "concatenating 3 segments (stream copy)")],
        ),
        Stage(
            "audio", "Extract audio", 1.6, 1,
            {"tool": "ffmpeg", "out": "16 kHz mono flac"},
        ),
        Stage(
            "asr", "ASR (ElevenLabs)", 4.0, 3,
            {"model": "scribe_v1", "diarize": "on"},
            script=[
                (0.1, "uploading audio (44.2 MiB)"),
                (0.4, "polling transcription…"),
                (0.95, "1,214 segments received"),
            ],
        ),
        Stage(
            "srt", "ASR → SRT", 0.8, 1,
            {"merge_gap": "280 ms"},
            script=[(0.6, "merged to 1,102 lines")],
        ),
        Stage(
            "prepass", "Pre-pass analysis", 5.0, 4,
            {"backend": "gemini-api", "model": "gemini-2.5-pro", "audio": "attached"},
            script=[
                (0.1, "audio uploaded to model"),
                (0.3, "cast identified: 9 performers"),
                (0.55, "glossary candidates: 37 entries"),
                (0.75, "segment synopsis written"),
                (0.95, "pre_pass.json saved"),
            ],
        ),
        Stage(
            "chunks", "Chunk translation", 0.0, 12,
            {"backend": "gemini-api", "concurrency": "4", "chunks": "18",
             "cache": ".chunks/"},
        ),
        Stage("refine", "Refine", 0.0, 0, {"backend": "claude"}, skipped=True),
        Stage(
            "glossary", "Glossary check", 3.0, 2,
            {"backend": "codex", "fixed_glossary": "42 entries"},
            script=[
                (0.3, "checking 42 fixed entries"),
                (0.85, "2 replacements applied"),
            ],
        ),
        Stage(
            "finalize", "Finalize (ASS + SRT)", 1.6, 1,
            {"style": "Netflix-TC", "punct": "full-width"},
            script=[
                (0.4, "punctuation normalized (Netflix-TC)"),
                (0.9, "wrote tp-250719.zh.ass + .srt"),
            ],
        ),
        Stage(
            "package", "Package (burn-in)", 4.0, 3,
            {"tool": "ffmpeg", "encoder": "hevc_nvenc"},
            script=[
                (0.1, "burning subtitles (hevc_nvenc)"),
                (0.9, "muxing audio streams"),
            ],
        ),
    ]


STAGE_RESULTS = {
    "metadata": "fod · air date 2025-07-19",
    "download": "3 segments · 1.48 GiB",
    "combine": "tp-250719.mp4 (48:12)",
    "audio": "tp-250719.flac · 44.2 MiB",
    "asr": "1,214 segments",
    "srt": "1,102 lines after merge",
    "prepass": "cast 9 · glossary 37 · $0.0810",
    "glossary": "2 fixes",
    "finalize": "ASS + SRT written",
    "package": "deliverables/tp-250719.mp4",
}


def make_side_tasks() -> list[SideTask]:
    return [
        SideTask(
            "date", "Date research", start_after="metadata", duration=7.0,
            params={"backend": "codex", "search": "web"},
            script=[
                (0.08, "searching TVer listing…"),
                (0.35, "candidate air date 2025-07-19 (TVer)"),
                (0.62, "cross-checking official site"),
                (0.95, "[green]verdict: 2025-07-19 · confidence high[/green]"),
            ],
        ),
        SideTask(
            "cover", "Cover image", start_after="combine", duration=14.0,
            params={"backend": "codex", "frames": "get_frames"},
            script=[
                (0.06, "extracting candidate frames (get_frames)"),
                (0.30, "codex agent iteration 1"),
                (0.55, "iteration 2 — selected frame 00:12:31"),
                (0.80, "compositing title overlay"),
                (0.96, "writing cover.png"),
            ],
        ),
    ]


SIDE_RESULTS = {
    "date": "2025-07-19 · confidence high",
    "cover": "cover.png (1920×1080)",
}


# ---------- fake artifacts shown when a stage is done ----------

FAKE_PREPASS_JSON = """\
{
  "show": "ザ・細かすぎて伝わらないモノマネ",
  "air_date": "2025-07-19",
  "cast": [
    {"name": "とにかく明るい安村", "zh": "安村"},
    {"name": "ジェラードン", "members": ["かみちぃ", "アタック西本", "海野"]}
  ],
  "glossary": [
    {"ja": "モノマネ", "zh": "模仿秀", "note": "show title term"},
    {"ja": "ツッコミ", "zh": "吐槽", "note": "fixed glossary"}
  ],
  "segments": [
    {"index": 1, "lines": "1-120", "synopsis": "開場・ルール説明"},
    {"index": 2, "lines": "121-402", "synopsis": "第1ブロック"}
  ],
  "cost_usd": 0.081
}"""

FAKE_GLOSSARY_REPORT = """\
# Glossary check report

**42 fixed entries checked · 2 replacements**

| line | before | after | entry |
|------|--------|-------|-------|
| 214 | 西本先生 | 阿塔克西本 | ジェラードン/アタック西本 |
| 890 | 吐嘈 | 吐槽 | ツッコミ |

No unresolved conflicts.
"""


def make_cover_preview() -> Text:
    """Fake cover.png preview rendered with half-block pixels.

    The real implementation would downscale cover.png with Pillow and
    render it via rich-pixels (or Sixel on supporting terminals).
    """
    width, height = 48, 28  # virtual pixels; two pixels per text row

    def px(x: int, y: int) -> tuple[int, int, int]:
        t = y / height  # dusk sky gradient
        r, g, b = int(30 + 150 * t), int(30 + 70 * t), int(90 + 110 * (1 - t))
        if (x - 34) ** 2 + (y - 9) ** 2 < 16:  # sun
            r, g, b = 255, 205, 90
        if y > 21:  # ground
            r, g, b = 40, 90, 60
        return r, g, b

    text = Text()
    for row in range(0, height, 2):
        for x in range(width):
            r1, g1, b1 = px(x, row)
            r2, g2, b2 = px(x, row + 1)
            text.append("▀", style=f"rgb({r1},{g1},{b1}) on rgb({r2},{g2},{b2})")
        text.append("\n")
    return text


def make_demo_cover_png() -> Path | None:
    """Render a demo cover.png (needs Pillow, which textual-image brings in)."""
    try:
        from PIL import Image as PILImage, ImageDraw, ImageFont
    except ImportError:
        return None
    path = Path(tempfile.gettempdir()) / "grillmaster_demo_cover.png"
    if path.exists():
        return path
    w, h = 640, 360
    img = PILImage.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        draw.line(
            [(0, y), (w, y)],
            fill=(int(30 + 150 * t), int(30 + 70 * t), int(90 + 110 * (1 - t))),
        )
    draw.ellipse([440, 70, 520, 150], fill=(255, 205, 90))
    draw.rectangle([0, 300, w, h], fill=(40, 90, 60))
    try:  # sized default fonts need Pillow >= 10.1
        big, small = ImageFont.load_default(48), ImageFont.load_default(20)
    except TypeError:
        big = small = ImageFont.load_default()
    draw.text((40, 110), "GRILL MASTER", font=big, fill=(255, 255, 255))
    draw.text((42, 180), "tp-250719 · demo cover artifact", font=small,
              fill=(230, 230, 230))
    img.save(path)
    return path


class Simulation:
    def __init__(self) -> None:
        self.stages = make_stages()
        self.side_tasks = make_side_tasks()
        # Per-item log lines keyed by stage/side-task key, already rich markup.
        self.logs: dict[str, list[str]] = {}
        self.index = -1
        self.elapsed = 0.0
        self.chunk_sim = ChunkSim()
        self.finished = False
        self._stages_exhausted = False
        self._advance()

    # ---------- helpers ----------

    @property
    def current(self) -> Stage | None:
        if 0 <= self.index < len(self.stages):
            return self.stages[self.index]
        return None

    @property
    def cost(self) -> float:
        return self.chunk_sim.cost + (0.081 if self.stage_done("prepass") else 0.0)

    def stage_done(self, key: str) -> bool:
        return any(s.key == key and s.state is State.DONE for s in self.stages)

    def items(self) -> list[Stage | SideTask]:
        """Sidebar order: pipeline stages first, then side tasks."""
        return [*self.stages, *self.side_tasks]

    def emit(self, key: str, message: str) -> None:
        self.logs.setdefault(key, []).append(
            f"[grey58]{fmt_clock(self.elapsed)}[/grey58] {message}"
        )

    # ---------- state machine ----------

    def _advance(self) -> None:
        cur = self.current
        if cur is not None:
            cur.state = State.DONE
            cur.progress = 1.0
            if cur.key == "chunks":
                cur.result = (
                    f"{self.chunk_sim.total} chunks · "
                    f"retries {self.chunk_sim.retries} · ${self.chunk_sim.cost:.4f}"
                )
            else:
                cur.result = STAGE_RESULTS.get(cur.key, "")
            self.emit(cur.key, f"[green]✔[/green] {cur.label} completed")
        self.index += 1
        while self.current is not None and self.current.skipped:
            self.current.state = State.SKIPPED
            self.emit(self.current.key, "[grey42]⊘ skipped (disabled)[/grey42]")
            self.index += 1
        if self.current is None:
            self._stages_exhausted = True
            if not self._side_tasks_done():
                self.emit("package", "[yellow1]main stages done — joining side tasks[/yellow1]")
        else:
            self.current.state = State.RUNNING
            self.emit(self.current.key, f"[yellow1]▶[/yellow1] {self.current.label} started")

    def _side_tasks_done(self) -> bool:
        return all(t.state is State.DONE for t in self.side_tasks)

    def _run_script(self, item: Stage | SideTask) -> None:
        while (
            item.script_emitted < len(item.script)
            and item.progress >= item.script[item.script_emitted][0]
        ):
            self.emit(item.key, item.script[item.script_emitted][1])
            item.script_emitted += 1

    def tick(self, dt: float) -> None:
        if self.finished:
            return
        self.elapsed += dt
        self._tick_side_tasks(dt)
        if self._stages_exhausted:
            if self._side_tasks_done():
                self.finished = True
                self.emit("package", "[bold green]Pipeline completed — deliverables ready.[/bold green]")
            return
        stage = self.current
        assert stage is not None
        stage.elapsed += dt
        if stage.key == "chunks":
            self.chunk_sim.tick(dt, lambda msg: self.emit("chunks", msg))
            stage.progress = self.chunk_sim.done / self.chunk_sim.total
            if self.chunk_sim.finished:
                self._advance()
        else:
            stage.progress = min(1.0, stage.elapsed / stage.duration)
            self._run_script(stage)
            if stage.progress >= 1.0:
                self._advance()

    def _tick_side_tasks(self, dt: float) -> None:
        for task in self.side_tasks:
            if task.state is State.PENDING and self.stage_done(task.start_after):
                task.state = State.RUNNING
                self.emit(
                    task.key,
                    f"[yellow1]▶[/yellow1] {task.label} started "
                    f"(triggered after {task.start_after})",
                )
            if task.state is State.RUNNING:
                task.elapsed += dt
                task.progress = min(1.0, task.elapsed / task.duration)
                self._run_script(task)
                if task.progress >= 1.0:
                    task.state = State.DONE
                    task.result = SIDE_RESULTS.get(task.key, "")
                    self.emit(task.key, f"[green]✔[/green] {task.label} completed")

    def total_progress(self) -> float:
        total_w = sum(s.weight for s in self.stages if not s.skipped)
        got = sum(s.weight * s.progress for s in self.stages if not s.skipped)
        return got / total_w


def fmt_clock(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def bar(progress: float, width: int = 30) -> ProgressBar:
    return ProgressBar(total=1.0, completed=progress, width=width)


class GrillDash(App):
    TITLE = "Owarai GrillMaster"

    CSS = """
    #header { height: 4; padding: 0 1; background: $surface; }
    #body { height: 1fr; }
    #stages-scroll { width: 40; border: round $primary-darken-2; padding: 0 1; }
    #detail-scroll { width: 1fr; border: round $primary-darken-2; padding: 0 1; }
    #stages, #detail { width: 100%; height: auto; }
    #cover-image { width: 76; height: 19; margin: 0 2; }
    #log { height: 9; border: round $surface-lighten-2; }
    """

    # Arrow keys use priority bindings so a focused scrollable widget
    # (e.g. the log pane) cannot swallow them.
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("p", "toggle_pause", "Pause"),
        ("r", "restart", "Restart"),
        ("f", "toggle_follow", "Follow"),
        Binding("up", "select_prev", "Prev stage", priority=True),
        Binding("down", "select_next", "Next stage", priority=True),
        Binding("k", "select_prev", "Prev stage", show=False),
        Binding("j", "select_next", "Next stage", show=False),
        Binding("pageup", "detail_scroll(-1)", "Scroll detail", priority=True),
        Binding("pagedown", "detail_scroll(1)", "Scroll detail", priority=True, show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with Horizontal(id="body"):
            with VerticalScroll(id="stages-scroll"):
                yield Static(id="stages")
            with VerticalScroll(id="detail-scroll"):
                yield Static(id="detail")
        yield RichLog(id="log", markup=True, wrap=False)
        yield Footer()

    def on_mount(self) -> None:
        self.paused = False
        self.follow = True
        self.selection = 0
        self._log_key: str | None = None
        self._log_count = 0
        self._scrolled_selection: int | None = None
        # Optional real-image rendering (Sixel on Windows Terminal >= 1.22,
        # auto-fallback to unicode cells): lazy import so the prototype also
        # runs with plain `--with textual`.
        try:
            from textual_image.widget import Image as TerminalImage

            self._image_cls = TerminalImage
            self._cover_path = make_demo_cover_png()
        except Exception:
            self._image_cls = None
            self._cover_path = None
        self.sim = Simulation()
        self.set_interval(TICK, self.tick)
        self.refresh_all()

    # ---------- actions ----------

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        self.refresh_all()

    def action_restart(self) -> None:
        self.sim = Simulation()
        self.paused = False
        self.follow = True
        self.selection = 0
        self._log_key = None
        self._log_count = 0
        self._scrolled_selection = None
        self.refresh_all()

    def action_toggle_follow(self) -> None:
        self.follow = not self.follow
        self.refresh_all()

    def action_select_prev(self) -> None:
        self.follow = False
        self.selection = (self.selection - 1) % len(self.sim.items())
        self._reset_detail_scroll()
        self.refresh_all()

    def action_select_next(self) -> None:
        self.follow = False
        self.selection = (self.selection + 1) % len(self.sim.items())
        self._reset_detail_scroll()
        self.refresh_all()

    def action_detail_scroll(self, direction: int) -> None:
        scroll = self.query_one("#detail-scroll", VerticalScroll)
        if direction < 0:
            scroll.scroll_page_up(animate=False)
        else:
            scroll.scroll_page_down(animate=False)

    def _reset_detail_scroll(self) -> None:
        self.query_one("#detail-scroll", VerticalScroll).scroll_home(animate=False)

    # ---------- tick ----------

    def tick(self) -> None:
        if not self.paused and not self.sim.finished:
            self.sim.tick(TICK)
        if self.follow:
            self._follow_running()
        self.refresh_all()

    def _follow_running(self) -> None:
        items = self.sim.items()
        for i, item in enumerate(items):
            if isinstance(item, Stage) and item.state is State.RUNNING:
                self.selection = i
                return
        # No running main stage: follow a running side task, else stay put.
        for i, item in enumerate(items):
            if isinstance(item, SideTask) and item.state is State.RUNNING:
                self.selection = i
                return

    def selected_item(self) -> Stage | SideTask:
        return self.sim.items()[self.selection]

    # ---------- rendering ----------

    def refresh_all(self) -> None:
        self.query_one("#header", Static).update(self.render_header())
        self.query_one("#stages", Static).update(self.render_stages())
        self.query_one("#detail", Static).update(self.render_detail())
        self._update_cover_widget()
        self.refresh_log()
        # Keep the selected row visible, but only when the selection actually
        # moved so manual mouse scrolling is not fought every tick.
        if self.selection != self._scrolled_selection:
            self._scroll_sidebar_to_selection()
            self._scrolled_selection = self.selection

    def _sidebar_line(self, index: int) -> int:
        """Content line of a sidebar row: 2 header lines, +3 before side tasks."""
        n_stages = len(self.sim.stages)
        if index < n_stages:
            return 2 + index
        return 2 + n_stages + 3 + (index - n_stages)

    def _scroll_sidebar_to_selection(self) -> None:
        scroll = self.query_one("#stages-scroll", VerticalScroll)
        height = scroll.container_size.height
        if height <= 0:
            return
        line = self._sidebar_line(self.selection)
        top = scroll.scroll_offset.y
        if line < top:
            scroll.scroll_to(y=line, animate=False)
        elif line >= top + height:
            scroll.scroll_to(y=line - height + 1, animate=False)

    def _update_cover_widget(self) -> None:
        """Mount/unmount the real-image cover preview below the detail panel."""
        item = self.selected_item()
        show = (
            self._image_cls is not None
            and self._cover_path is not None
            and not (self.sim.finished and self.follow)
            and item.key == "cover"
            and item.state is State.DONE
        )
        existing = self.query("#cover-image")
        if show and not existing:
            self.query_one("#detail-scroll", VerticalScroll).mount(
                self._image_cls(str(self._cover_path), id="cover-image")
            )
        elif not show and existing:
            existing.remove()

    def refresh_log(self) -> None:
        item = self.selected_item()
        widget = self.query_one("#log", RichLog)
        widget.border_title = f"log · {item.label}"
        lines = self.sim.logs.get(item.key, [])
        if item.key != self._log_key:
            widget.clear()
            self._log_key = item.key
            self._log_count = 0
        for line in lines[self._log_count:]:
            widget.write(Text.from_markup(line))
        self._log_count = len(lines)

    def render_header(self):
        sim = self.sim
        title = Text.from_markup(
            "[bold magenta]Owarai GrillMaster[/bold magenta]  [grey58]·[/grey58]  "
            "project [bold]tp-250719[/bold]  [grey58]·[/grey58]  "
            "ザ・細かすぎて伝わらないモノマネ (2025-07-19)"
        )
        done_stages = sum(1 for s in sim.stages if s.state is State.DONE)
        active = sum(1 for s in sim.stages if not s.skipped)
        status = "[bold green]COMPLETED[/bold green]" if sim.finished else (
            "[bold yellow1]PAUSED[/bold yellow1]" if self.paused
            else "[bold cyan]RUNNING[/bold cyan]"
        )
        follow = "[green]follow[/green]" if self.follow else "[grey42]follow off[/grey42]"
        line = Table.grid(padding=(0, 2))
        line.add_column()
        line.add_column()
        line.add_column()
        line.add_row(
            bar(sim.total_progress(), width=40),
            Text.from_markup(
                f"[bold]{sim.total_progress() * 100:3.0f}%[/bold]  "
                f"stage {done_stages}/{active}  ·  elapsed {fmt_clock(sim.elapsed)}  ·  "
                f"cost [green]${sim.cost:.4f}[/green]  ·  {follow}"
            ),
            Text.from_markup(status),
        )
        return Group(title, Text(), line)

    def _sidebar_row(self, item: Stage | SideTask, selected: bool) -> Text:
        icon, style = STATE_ICON[item.state]
        text = Text()
        text.append("❯" if selected else " ", style="bold cyan")
        text.append(f"{icon} ", style=style)
        name_style = "bold white" if item.state is State.RUNNING else (
            "grey42" if item.state in (State.PENDING, State.SKIPPED) else ""
        )
        text.append(item.label, style=name_style)
        if item.state is State.RUNNING:
            text.append(f"  {item.progress * 100:3.0f}%", style="yellow1")
        elif item.state is State.DONE:
            text.append(f"  {fmt_clock(item.elapsed)}", style="grey58")
        if selected:
            text.stylize("on grey23")
        return text

    def render_stages(self):
        items = self.sim.items()
        rows = [Text.from_markup("[bold underline]Pipeline[/bold underline]"), Text()]
        for i, item in enumerate(items):
            if isinstance(item, SideTask) and isinstance(items[i - 1], Stage):
                rows.append(Text())
                rows.append(Text.from_markup("[bold underline]Side tasks[/bold underline]"))
                rows.append(Text())
            rows.append(self._sidebar_row(item, selected=(i == self.selection)))
        return Group(*rows)

    def render_detail(self):
        if self.sim.finished and self.follow:
            return self.render_summary()
        item = self.selected_item()
        params = Table.grid(padding=(0, 2))
        params.add_column(style="grey58")
        params.add_column(style="cyan")
        for key, value in item.params.items():
            params.add_row(key, value)
        if isinstance(item, SideTask):
            params.add_row("trigger", f"after {item.start_after}")
        body: list = [params, Text()]
        if isinstance(item, Stage) and item.key == "chunks":
            if item.state is not State.PENDING:
                body += self.render_chunks()
            else:
                body += [Text("waiting…", style="grey42")]
        elif isinstance(item, Stage) and item.key == "prepass" and item.state is State.RUNNING:
            body += self.render_prepass(item)
        elif isinstance(item, Stage) and item.key == "download" and item.state is State.RUNNING:
            body += self.render_download(item)
        elif item.state is State.RUNNING:
            body += [
                bar(item.progress, width=44),
                Text(
                    f"\n{item.progress * 100:.0f}% · {fmt_clock(item.elapsed)} elapsed",
                    style="grey58",
                ),
            ]
        elif item.state is State.DONE:
            body += [
                Text.from_markup(
                    f"[green]✔ completed[/green] in {fmt_clock(item.elapsed)}"
                ),
            ]
        elif item.state is State.SKIPPED:
            body += [Text("skipped (disabled for this run)", style="grey42")]
        else:
            body += [Text("waiting…", style="grey42")]
        if item.result:
            body += [
                Text(),
                Text.from_markup(f"[grey58]result[/grey58]  [bold]{item.result}[/bold]"),
            ]
        artifact = self.render_artifact(item)
        if artifact is not None:
            body += [Text(), *artifact]
        border = {
            State.RUNNING: "cyan",
            State.DONE: "green",
            State.SKIPPED: "grey42",
            State.PENDING: "grey42",
        }[item.state]
        return Panel(
            Group(*body), title=f"[bold]{item.label}[/bold]", border_style=border
        )

    def render_artifact(self, item: Stage | SideTask) -> list | None:
        """Inline artifact preview for completed stages (scroll with PgUp/PgDn)."""
        if item.state is not State.DONE:
            return None
        if item.key == "prepass":
            return [
                Text.from_markup("[grey58]artifact[/grey58]  pre_pass.json"),
                Text(),
                Syntax(
                    FAKE_PREPASS_JSON, "json", line_numbers=True,
                    background_color="default", word_wrap=True,
                ),
            ]
        if item.key == "glossary":
            return [
                Text.from_markup("[grey58]artifact[/grey58]  glossary_report.md"),
                Text(),
                Markdown(FAKE_GLOSSARY_REPORT),
            ]
        if item.key == "cover":
            if self._image_cls is not None and self._cover_path is not None:
                # The real image renders in a separate widget mounted below
                # this panel (Sixel where supported, unicode cells otherwise).
                return [
                    Text.from_markup(
                        "[grey58]artifact[/grey58]  cover.png "
                        "[grey42](rendered below via textual-image)[/grey42]"
                    ),
                ]
            return [
                Text.from_markup("[grey58]artifact[/grey58]  cover.png"),
                Text(),
                make_cover_preview(),
                Text.from_markup(
                    "[grey42]half-block preview · install textual-image for "
                    "Sixel rendering[/grey42]"
                ),
            ]
        return None

    def render_download(self, stage: Stage):
        speed = 0.0 if stage.progress >= 1 else 8.5 + 3.0 * random.random()
        segs = []
        for i in range(3):
            seg_progress = min(1.0, max(0.0, stage.progress * 3 - i))
            row = Table.grid(padding=(0, 1))
            row.add_column(width=12)
            row.add_column()
            row.add_column(width=6)
            row.add_row(
                Text(f"segment {i + 1}/3", style="grey58"),
                bar(seg_progress, width=36),
                Text(f"{seg_progress * 100:3.0f}%"),
            )
            segs.append(row)
        info = Text.from_markup(
            f"\n[grey58]speed[/grey58] {speed:4.1f} MiB/s   "
            f"[grey58]eta[/grey58] {fmt_clock(max(0.0, stage.duration - stage.elapsed))}"
        )
        return [*segs, info]

    def render_prepass(self, stage: Stage):
        rows = []
        per = 1.0 / len(PRE_PASS_STEPS)
        for i, step in enumerate(PRE_PASS_STEPS):
            if stage.progress >= (i + 1) * per:
                rows.append(Text.from_markup(f"  [green]✔[/green] {step}"))
            elif stage.progress >= i * per:
                rows.append(Text.from_markup(f"  [yellow1]▶[/yellow1] [bold]{step}[/bold]"))
            else:
                rows.append(Text.from_markup(f"  [grey42]○ {step}[/grey42]"))
        rows.append(Text())
        rows.append(bar(stage.progress, width=44))
        return rows

    def render_chunks(self):
        sim = self.sim.chunk_sim
        grid = Text()
        for i, chunk in enumerate(sim.chunks):
            grid.append(f" {chunk.index + 1:02d} ", style=CHUNK_STYLE[chunk.state])
            grid.append(" ")
            if (i + 1) % 9 == 0:
                grid.append("\n\n")
        legend = Text.from_markup(
            "[grey58 on grey19] ░░ [/grey58 on grey19] pending   "
            "[black on yellow1] ▶▶ [/black on yellow1] active   "
            "[black on dark_orange] ↻↻ [/black on dark_orange] retrying   "
            "[black on green3] ✔✔ [/black on green3] done"
        )
        workers = Table(box=None, padding=(0, 2), show_header=True, header_style="grey58")
        workers.add_column("worker")
        workers.add_column("chunk")
        workers.add_column("lines")
        workers.add_column("progress")
        workers.add_column("state")
        slot = 1
        for chunk in sim.chunks:
            if chunk.state in (ChunkState.ACTIVE, ChunkState.RETRY):
                workers.add_row(
                    f"W{slot}",
                    f"#{chunk.index + 1:02d}",
                    f"{chunk.lines[0]}–{chunk.lines[1]}",
                    bar(chunk.progress, width=22),
                    "[dark_orange]retry[/dark_orange]"
                    if chunk.state is ChunkState.RETRY
                    else "[yellow1]translating[/yellow1]",
                )
                slot += 1
        stats = Text.from_markup(
            f"\n[bold]{sim.done}[/bold]/{sim.total} done  ·  "
            f"retries [dark_orange]{sim.retries}[/dark_orange]  ·  "
            f"cost [green]${sim.cost:.4f}[/green]"
        )
        return [grid, Text(), legend, Text(), workers, stats]

    def render_summary(self):
        table = Table.grid(padding=(0, 2))
        table.add_column(style="grey58")
        table.add_column(style="green")
        table.add_row("subtitles", "projects/tp-250719/subtitles/tp-250719.zh.srt")
        table.add_row("styled ass", "projects/tp-250719/subtitles/tp-250719.zh.ass")
        table.add_row("burned mp4", "projects/tp-250719/deliverables/tp-250719.mp4")
        table.add_row("cover", "projects/tp-250719/cover.png")
        table.add_row("air date", "2025-07-19 (confidence high)")
        table.add_row("total cost", f"${self.sim.cost:.4f}")
        table.add_row("wall time", fmt_clock(self.sim.elapsed))
        hint = Text.from_markup(
            "\n[grey58]↑/↓ browse stage history · each stage keeps its params, "
            "result and log[/grey58]"
        )
        return Panel(
            Group(
                Text.from_markup("[bold green]✔ Pipeline completed[/bold green]\n"),
                table,
                hint,
            ),
            title="[bold]Summary[/bold]",
            border_style="green",
        )


if __name__ == "__main__":
    GrillDash().run()
