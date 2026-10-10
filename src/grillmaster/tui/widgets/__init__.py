"""The dashboard's widgets; each renders from `PipelineState` and the `View`."""

from __future__ import annotations

from grillmaster.tui.widgets._common import View
from grillmaster.tui.widgets.activity_log import ActivityLog, LogPane
from grillmaster.tui.widgets.chunk_board import ChunkBoard
from grillmaster.tui.widgets.header import Header
from grillmaster.tui.widgets.session_table import SessionTable
from grillmaster.tui.widgets.stage_detail import StageDetail
from grillmaster.tui.widgets.stage_list import StageList, row_line
from grillmaster.tui.widgets.summary import Summary

__all__ = [
    "ActivityLog",
    "ChunkBoard",
    "Header",
    "LogPane",
    "SessionTable",
    "StageDetail",
    "StageList",
    "Summary",
    "View",
    "row_line",
]
