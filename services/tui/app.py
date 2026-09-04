"""Full-screen Textual dashboard for one pipeline run.

Layout (approved via scripts/tui_prototype.py): header with weighted total
progress, selectable sidebar of stages + side tasks, per-stage detail panel
(params, live sub-task bars, chunk board, results, artifact previews), and a
log pane showing only the selected item's log.

The app renders from the shared ``PipelineState`` on a 10 Hz timer; it never
receives events directly.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from time import monotonic

from rich.console import Group
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Footer, RichLog, Static

from .artifacts import ArtifactCache
from .state import (
    ChunkState,
    ItemState,
    ItemView,
    PipelineState,
)

ABORT_CONFIRM_WINDOW = 3.0  # seconds between the two `q` presses

CLIPBOARD_COMMAND = {
    "win32": ["clip.exe"],
    "darwin": ["pbcopy"],
}.get(sys.platform, ["xclip", "-selection", "clipboard"])

STATE_ICON = {
    ItemState.PENDING: ("○", "grey58"),
    ItemState.RUNNING: ("▶", "bold yellow1"),
    ItemState.DONE: ("✔", "green3"),
    ItemState.CACHED: ("✔", "cyan"),
    ItemState.DISABLED: ("⊘", "grey42"),
    ItemState.FAILED: ("✘", "bold red"),
}

CHUNK_STYLE = {
    ChunkState.PENDING: "grey58 on grey19",
    ChunkState.ACTIVE: "black on yellow1",
    ChunkState.DONE: "black on green3",
    ChunkState.FAILED: "white on red",
}

LOG_LEVEL_STYLE = {
    "DEBUG": "grey58",
    "INFO": "",
    "SUCCESS": "green3",
    "WARNING": "yellow1",
    "ERROR": "bold red",
    "CRITICAL": "bold red",
}


def put_on_clipboard(text: str) -> None:
    """Hand `text` to the OS clipboard, raising if the helper fails.

    Textual's own copy rides OSC 52, which conhost — the terminal this
    normally runs in — ignores, so the log pane could only be read, never
    copied out of a failed run. The platform helper actually lands it.
    clip.exe wants UTF-8 with no BOM: a BOM arrives as a literal
    character in the pasted text.
    """
    subprocess.run(
        CLIPBOARD_COMMAND, input=text.encode("utf-8"), check=True
    )


def fmt_clock(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def bar(progress: float, width: int = 30) -> ProgressBar:
    return ProgressBar(total=1.0, completed=progress, width=width)


class GrillMasterApp(App):
    TITLE = "Owarai GrillMaster"

    CSS = """
    #header { height: 4; padding: 0 1; background: $surface; }
    #body { height: 1fr; }
    #stages-scroll { width: 42; border: round $primary-darken-2; padding: 0 1; }
    #detail-scroll { width: 1fr; border: round $primary-darken-2; padding: 0 1; }
    #stages, #detail { width: 100%; height: auto; }
    #cover-image { width: 76; height: 19; margin: 0 2; }
    #log { height: 9; border: round $surface-lighten-2; }
    """

    BINDINGS = [
        ("q", "quit_or_abort", "Quit"),
        ("r", "retry", "Retry"),
        ("f", "toggle_follow", "Follow"),
        ("o", "open_artifact", "Open artifact"),
        ("c", "copy_log", "Copy log"),
        Binding("up", "select_prev", "Prev stage", priority=True),
        Binding("down", "select_next", "Next stage", priority=True),
        Binding("k", "select_prev", "Prev stage", show=False),
        Binding("j", "select_next", "Next stage", show=False),
        Binding("pageup", "detail_scroll(-1)", "Scroll detail", priority=True),
        Binding(
            "pagedown", "detail_scroll(1)", "Scroll detail",
            priority=True, show=False,
        ),
        Binding("ctrl+c", "quit_or_abort", "Quit", show=False, priority=True),
    ]

    def __init__(
        self,
        state: PipelineState,
        on_retry: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__()
        self.state = state
        self.on_retry = on_retry
        self.artifacts = ArtifactCache()

    def compose(self) -> ComposeResult:
        yield Static(id="header")
        with Horizontal(id="body"):
            with VerticalScroll(id="stages-scroll"):
                yield Static(id="stages")
            with VerticalScroll(id="detail-scroll"):
                yield Static(id="detail")
        yield RichLog(id="log", markup=False, wrap=False)
        yield Footer()

    def on_mount(self) -> None:
        self.follow = True
        self.selection = 0
        self._log_key: str | None = None
        self._log_count = 0
        self._scrolled_selection: int | None = None
        self._abort_armed_at: float | None = None
        try:
            from textual_image.widget import Image as TerminalImage

            self._image_cls = TerminalImage
        except Exception:
            self._image_cls = None
        self.set_interval(0.1, self.tick)
        self.refresh_all()

    # ---------- actions ----------

    def action_quit_or_abort(self) -> None:
        if self.state.finished:
            self.exit(1 if self.state.failed else 0)
            return
        now = monotonic()
        if (
            self._abort_armed_at is not None
            and now - self._abort_armed_at <= ABORT_CONFIRM_WINDOW
        ):
            # Second press: leave. The pipeline daemon thread dies with the
            # process; every stage is resumable on the next run.
            self.exit(130)
            return
        self._abort_armed_at = now
        self.notify(
            "Pipeline is running — press q again within 3s to abort "
            "(the project is resumable).",
            severity="warning",
        )

    def action_retry(self) -> None:
        if not (self.state.finished and self.state.failed):
            self.notify(
                "Retry is available after a failure.", severity="information"
            )
            return
        if self.on_retry is None or not self.on_retry():
            return
        # Items get rebuilt by the new attempt's pipeline_started; drop the
        # log pane trackers so it re-renders from the fresh buffers.
        self._log_key = None
        self._log_count = 0
        self.follow = True
        self.notify("Retrying — resuming from the failed stage.")

    def action_toggle_follow(self) -> None:
        self.follow = not self.follow
        self.refresh_all()

    def action_select_prev(self) -> None:
        self._move_selection(-1)

    def action_select_next(self) -> None:
        self._move_selection(1)

    def _move_selection(self, delta: int) -> None:
        with self.state.lock:
            count = len(self.state.items)
        if count == 0:
            return
        self.follow = False
        self.selection = (self.selection + delta) % count
        self._reset_detail_scroll()
        self.refresh_all()

    def action_detail_scroll(self, direction: int) -> None:
        scroll = self.query_one("#detail-scroll", VerticalScroll)
        if direction < 0:
            scroll.scroll_page_up(animate=False)
        else:
            scroll.scroll_page_down(animate=False)

    def action_open_artifact(self) -> None:
        item = self.selected_item()
        if item is None:
            return
        preview = self.artifacts.preview(self.state.project, item.key)
        if preview is None or preview.open_path is None:
            self.notify("No artifact for this stage.", severity="information")
            return
        try:
            os.startfile(preview.open_path)  # noqa: S606 - user-initiated
        except OSError as e:
            self.notify(f"Could not open artifact: {e}", severity="error")

    def action_copy_log(self) -> None:
        """Copy the whole log buffer, not just the tail the pane shows."""
        item = self.selected_item()
        with self.state.lock:
            if item is None:
                label, lines = "pipeline", list(self.state.pipeline_log)
            else:
                label, lines = item.label, list(item.log)
        if not lines:
            self.notify("Nothing in this log yet.", severity="information")
            return
        text = "\n".join([f"# {label}", *(line for _, line in lines)])
        try:
            put_on_clipboard(text)
        except (OSError, subprocess.SubprocessError) as e:
            self.notify(f"Could not copy the log: {e}", severity="error")
            return
        self.notify(f"Copied {len(lines)} log lines to the clipboard.")

    def _reset_detail_scroll(self) -> None:
        self.query_one("#detail-scroll", VerticalScroll).scroll_home(
            animate=False
        )

    # ---------- tick / selection ----------

    def tick(self) -> None:
        if self.follow:
            self._follow_running()
        self.refresh_all()

    def _follow_running(self) -> None:
        with self.state.lock:
            items = list(self.state.items)
        running_side = None
        for i, item in enumerate(items):
            if item.state is ItemState.RUNNING:
                if item.kind == "stage":
                    self.selection = i
                    return
                if running_side is None:
                    running_side = i
        if running_side is not None:
            self.selection = running_side

    def selected_item(self) -> ItemView | None:
        with self.state.lock:
            items = self.state.items
            if not items:
                return None
            self.selection = min(self.selection, len(items) - 1)
            return items[self.selection]

    # ---------- rendering ----------

    def refresh_all(self) -> None:
        try:
            with self.state.lock:
                self.query_one("#header", Static).update(self.render_header())
                self.query_one("#stages", Static).update(self.render_stages())
                self.query_one("#detail", Static).update(self.render_detail())
                self._update_cover_widget()
                self.refresh_log()
        except NoMatches:
            # A timer tick can land while the app is shutting down and the
            # widgets are already gone.
            return
        if self.selection != self._scrolled_selection:
            self._scroll_sidebar_to_selection()
            self._scrolled_selection = self.selection

    def render_header(self):
        state = self.state
        project_id = getattr(state.project, "id", "") or ""
        project_name = getattr(state.project, "name", None) or ""
        title = Text.assemble(
            ("Owarai GrillMaster", "bold magenta"),
            ("  ·  project ", "grey58"),
            (str(project_id), "bold"),
        )
        if project_name and project_name != project_id:
            title.append("  ·  ", "grey58")
            title.append(str(project_name))
        stages = [i for i in state.items if i.kind == "stage"]
        done = sum(
            1
            for i in stages
            if i.state in (ItemState.DONE, ItemState.CACHED)
        )
        active = sum(1 for i in stages if i.state is not ItemState.DISABLED)
        if state.failed:
            status = Text("FAILED", "bold red")
        elif state.finished:
            status = Text("COMPLETED", "bold green")
        else:
            status = Text("RUNNING", "bold cyan")
        follow = (
            Text("follow", "green")
            if self.follow
            else Text("follow off", "grey42")
        )
        line = Table.grid(padding=(0, 2))
        line.add_column()
        line.add_column()
        line.add_column()
        progress = state.total_progress()
        info = Text()
        info.append(f"{progress * 100:3.0f}%", "bold")
        info.append(f"  stage {done}/{active}  ·  ")
        info.append(f"elapsed {fmt_clock(state.wall_elapsed())}  ·  cost ")
        info.append(f"${state.total_cost():.4f}", "green")
        info.append("  ·  ")
        info.append(follow)
        line.add_row(bar(progress, width=40), info, status)
        return Group(title, Text(), line)

    def _sidebar_row(self, item: ItemView, selected: bool) -> Text:
        icon, style = STATE_ICON[item.state]
        text = Text()
        text.append("❯" if selected else " ", style="bold cyan")
        text.append(f"{icon} ", style=style)
        name_style = (
            "bold white"
            if item.state is ItemState.RUNNING
            else (
                "grey42"
                if item.state in (ItemState.PENDING, ItemState.DISABLED)
                else ""
            )
        )
        text.append(item.label, style=name_style)
        if item.state is ItemState.RUNNING:
            if item.key == "chunks" and self.state.chunks.total > 0:
                board = self.state.chunks
                text.append(
                    f"  {board.done}/{board.total}", style="yellow1"
                )
            else:
                text.append(
                    f"  {fmt_clock(item.live_elapsed())}", style="yellow1"
                )
        elif item.state is ItemState.DONE:
            text.append(f"  {fmt_clock(item.elapsed)}", style="grey58")
        elif item.state is ItemState.CACHED:
            text.append("  cached", style="cyan")
        if selected:
            text.stylize("on grey23")
        return text

    def render_stages(self):
        items = self.state.items
        rows: list = [Text("Pipeline", style="bold underline"), Text()]
        if not items:
            rows.append(Text("starting…", style="grey42"))
        side_header_added = False
        for i, item in enumerate(items):
            if item.kind == "side_task" and not side_header_added:
                rows.append(Text())
                rows.append(Text("Side tasks", style="bold underline"))
                rows.append(Text())
                side_header_added = True
            rows.append(self._sidebar_row(item, selected=(i == self.selection)))
        return Group(*rows)

    def render_detail(self):
        state = self.state
        if state.finished and self.follow:
            return self.render_summary()
        item = self.selected_item()
        if item is None:
            return Panel(
                Text("waiting for pipeline…", style="grey42"),
                border_style="grey42",
            )
        params = Table.grid(padding=(0, 2))
        params.add_column(style="grey58")
        params.add_column(style="cyan")
        for key, value in item.params.items():
            params.add_row(key, value)
        body: list = [params, Text()] if item.params else []
        if item.state is ItemState.RUNNING:
            body += self._render_running(item)
        elif item.state in (ItemState.DONE, ItemState.CACHED):
            if item.state is ItemState.DONE:
                body.append(
                    Text.assemble(
                        ("✔ completed", "green"),
                        (f" in {fmt_clock(item.elapsed)}", ""),
                    )
                )
            else:
                body.append(
                    Text("✔ already complete (previous run)", style="cyan")
                )
            if item.key == "chunks" and state.chunks.total > 0:
                body.append(Text())
                body += self._render_chunk_board()
            if item.result:
                body += [
                    Text(),
                    Text.assemble(
                        ("result  ", "grey58"), (item.result, "bold")
                    ),
                ]
            preview = self.artifacts.preview(state.project, item.key)
            if preview is not None:
                body += [Text(), *preview.renderables]
                if preview.image_path is not None:
                    if not preview.image_path.exists():
                        # Archive may have moved the project folder away.
                        hint = "(file moved — no longer previewable)"
                    elif self._image_cls is not None:
                        hint = "(rendered below via textual-image)"
                    else:
                        hint = "(install textual-image for inline preview)"
                    body.append(Text(hint, style="grey42"))
        elif item.state is ItemState.FAILED:
            body.append(Text("✘ failed", style="bold red"))
            if item.error:
                body += [Text(), Text(item.error, style="red")]
        elif item.state is ItemState.DISABLED:
            body.append(Text("disabled for this run", style="grey42"))
        else:
            body.append(Text("waiting…", style="grey42"))
        border = {
            ItemState.RUNNING: "cyan",
            ItemState.DONE: "green",
            ItemState.CACHED: "cyan",
            ItemState.DISABLED: "grey42",
            ItemState.PENDING: "grey42",
            ItemState.FAILED: "red",
        }[item.state]
        return Panel(
            Group(*body),
            title=f"[bold]{item.label}[/bold]",
            border_style=border,
        )

    def _render_running(self, item: ItemView) -> list:
        body: list = []
        if item.key == "chunks":
            body += self._render_chunk_board()
        for task_bar in item.bars.values():
            if task_bar.done:
                continue
            row = Table.grid(padding=(0, 1))
            row.add_column()
            row.add_column(width=8)
            row.add_row(
                bar(task_bar.fraction, width=40),
                Text(f"{task_bar.fraction * 100:3.0f}%"),
            )
            body.append(
                Text(task_bar.description or task_bar.label, style="grey58")
            )
            body.append(row)
        body.append(Text())
        body.append(
            Text(
                f"{fmt_clock(item.live_elapsed())} elapsed", style="grey58"
            )
        )
        return body

    def _render_chunk_board(self) -> list:
        board = self.state.chunks
        if board.total == 0:
            return [Text("preparing chunks…", style="grey42")]
        grid = Text()
        for i in range(board.total):
            cell = board.cells.get(i)
            cell_state = cell.state if cell is not None else ChunkState.PENDING
            grid.append(f" {i + 1:02d} ", style=CHUNK_STYLE[cell_state])
            grid.append(" ")
            if (i + 1) % 9 == 0 and i + 1 < board.total:
                grid.append("\n\n")
        workers = Table(
            box=None, padding=(0, 2), show_header=True, header_style="grey58"
        )
        workers.add_column("chunk")
        workers.add_column("lines")
        workers.add_column("elapsed")
        for cell in sorted(board.active, key=lambda c: c.index):
            workers.add_row(
                f"#{cell.index + 1:02d}",
                f"{cell.from_index}–{cell.to_index}",
                fmt_clock(cell.live_elapsed()),
            )
        stats = Text()
        stats.append(f"{board.done}", "bold")
        stats.append(f"/{board.total} done  ·  retries ")
        stats.append(str(board.retries), "dark_orange")
        stats.append("  ·  failed ")
        stats.append(str(board.failed), "red" if board.failed else "")
        stats.append("  ·  cost ")
        stats.append(f"${board.cost:.4f}", "green")
        parts: list = [grid, Text()]
        if board.active:
            parts.append(workers)
        parts.append(stats)
        parts.append(Text())
        return parts

    def render_summary(self):
        state = self.state
        if state.failed:
            body: list = [
                Text("✘ Pipeline failed", style="bold red"),
                Text(),
            ]
            if state.error:
                body.append(Text(state.error, style="red"))
            body.append(Text())
            body.append(
                Text(
                    "r retries from the failed stage (completed stages are "
                    "cached) · select the failed stage for its log.",
                    style="grey58",
                )
            )
            return Panel(
                Group(*body),
                title="[bold]Failed[/bold]",
                border_style="red",
            )
        table = Table.grid(padding=(0, 2))
        table.add_column(style="grey58")
        table.add_column(style="green")
        for label, attr in (
            ("subtitles", "finalized_srt_path"),
            ("styled ass", "ass_path"),
            ("cover", "poster_cover_path"),
        ):
            path = getattr(state.project, attr, None)
            if path is not None and Path(str(path)).exists():
                table.add_row(label, str(path))
        table.add_row("total cost", f"${state.total_cost():.4f}")
        table.add_row("wall time", fmt_clock(state.wall_elapsed()))
        hint = Text(
            "\n↑/↓ browse stage history · o opens the selected artifact",
            style="grey58",
        )
        return Panel(
            Group(
                Text("✔ Pipeline completed\n", style="bold green"),
                table,
                hint,
            ),
            title="[bold]Summary[/bold]",
            border_style="green",
        )

    # ---------- log pane ----------

    def refresh_log(self) -> None:
        item = self.selected_item()
        widget = self.query_one("#log", RichLog)
        if item is None:
            lines = list(self.state.pipeline_log)
            key = "__pipeline__"
            widget.border_title = "log"
        else:
            lines = list(item.log)
            key = item.key
            widget.border_title = f"log · {item.label}"
        if key != self._log_key:
            widget.clear()
            self._log_key = key
            self._log_count = 0
        for level, text in lines[self._log_count:]:
            style = LOG_LEVEL_STYLE.get(level, "")
            widget.write(Text(text, style=style))
        self._log_count = len(lines)

    # ---------- sidebar scroll / cover image ----------

    def _sidebar_line(self, index: int) -> int:
        """Content line of a sidebar row: 2 header lines, +3 before side tasks."""
        with self.state.lock:
            n_stages = sum(
                1 for item in self.state.items if item.kind == "stage"
            )
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
        item = self.selected_item()
        preview = (
            self.artifacts.preview(self.state.project, item.key)
            if item is not None
            and item.state in (ItemState.DONE, ItemState.CACHED)
            and not (self.state.finished and self.follow)
            else None
        )
        # The preview is cached at first render, but the archive stage can
        # move the whole project folder afterwards — re-check the file each
        # tick so a stale path never reaches the image widget.
        show = (
            self._image_cls is not None
            and preview is not None
            and preview.image_path is not None
            and preview.image_path.exists()
        )
        existing = self.query("#cover-image")
        if show and not existing:
            try:
                widget = self._image_cls(
                    str(preview.image_path), id="cover-image"
                )
            except Exception:
                # File vanished between the exists() check and PIL opening
                # it (archive runs concurrently) — skip the inline render.
                return
            self.query_one("#detail-scroll", VerticalScroll).mount(widget)
        elif not show and existing:
            existing.remove()
