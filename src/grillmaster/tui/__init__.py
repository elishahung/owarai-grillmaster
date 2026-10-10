"""The full-screen run dashboard, driven only by `grillmaster.events`.

`run_with_tui(work)` runs `work(sink)` on a worker thread under the Textual
app and returns its result. `TuiSink` queues events from any thread;
`PipelineState` is the pure reducer the app renders from (design §8.3).
The package never imports the pipeline, stages, project or config.
"""

from __future__ import annotations

from grillmaster.tui.app import AppExit, GrillMasterApp
from grillmaster.tui.run import RunAborted, run_with_tui
from grillmaster.tui.sink import TuiSink, log_bridge
from grillmaster.tui.state import PipelineState

__all__ = [
    "AppExit",
    "GrillMasterApp",
    "PipelineState",
    "RunAborted",
    "TuiSink",
    "log_bridge",
    "run_with_tui",
]
