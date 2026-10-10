from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from grillmaster.events.context import stage_scope, task_scope
from grillmaster.events.sinks import ConsoleSink, JsonlSink, describe
from grillmaster.events.types import (
    ActivityKind,
    AgentActivity,
    AgentSessionFinished,
    AgentSessionStarted,
    LogLine,
    PlanEntry,
    PlanKind,
    ProgressAdvanced,
    RunFinished,
    RunOutcome,
    RunStarted,
    SessionOutcome,
    SkipReason,
    StepCompleted,
    StepFailed,
    StepSkipped,
    StepStarted,
)

if TYPE_CHECKING:
    from pathlib import Path

    from loguru import Record

    from grillmaster.events.types import Event

FIXED_TIME = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
STAGE = PlanKind.STAGE


def test_console_sink_logs_described_events_only(log_records: list[Record]):
    sink = ConsoleSink()
    sink.emit(StepStarted("prepass", STAGE))
    sink.emit(LogLine("INFO", "already logged"))
    assert [(r["level"].name, r["message"]) for r in log_records] == [
        ("INFO", "[prepass] started"),
    ]


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (
            RunStarted(
                "ep1",
                (
                    PlanEntry("download", "Download", PlanKind.STAGE),
                    PlanEntry("cover", "Cover", PlanKind.SIDE_TASK, enabled=False),
                ),
            ),
            ("INFO", "Run ep1: 1/2 steps planned"),
        ),
        (RunFinished(RunOutcome.COMPLETED), ("SUCCESS", "Run completed")),
        (RunFinished(RunOutcome.FAILED, "boom"), ("ERROR", "Run failed: boom")),
        (StepStarted("prepass", STAGE), ("INFO", "[prepass] started")),
        (
            StepCompleted("prepass", STAGE, 412.04, "12 characters"),
            ("SUCCESS", "[prepass] done in 412.0s: 12 characters"),
        ),
        (
            StepCompleted("archive", PlanKind.DELIVERY, 3.0),
            ("SUCCESS", "[archive] done in 3.0s"),
        ),
        (
            StepSkipped("asr", STAGE, SkipReason.ALREADY_COMPLETE),
            ("INFO", "[asr] skipped (already-complete)"),
        ),
        (
            StepFailed("cover", PlanKind.SIDE_TASK, "quota"),
            ("ERROR", "[cover] failed: quota"),
        ),
        (
            AgentSessionStarted("refine", "refine", "codex", "gpt-5.5", "medium"),
            ("INFO", "<refine> session started on codex/gpt-5.5/medium"),
        ),
        (
            AgentActivity("refine", ActivityKind.TOOL_CALL, "get_frames 62.5"),
            ("DEBUG", "<refine> tool_call: get_frames 62.5"),
        ),
        (
            AgentSessionFinished("refine", SessionOutcome.OUTPUT_ERROR, 30.0, 3),
            ("WARNING", "<refine> session output_error in 30.0s, 3 repair(s)"),
        ),
        (LogLine("INFO", "already logged"), None),
        (ProgressAdvanced("chunks"), None),
    ],
)
def test_describe(event: Event, expected: tuple[str, str] | None):
    assert describe(event) == expected


def test_jsonl_sink_writes_type_fields_and_timestamp(tmp_path: Path):
    path = tmp_path / "logs" / "events.jsonl"
    with JsonlSink(path, clock=lambda: FIXED_TIME) as sink:
        sink.emit(
            RunStarted(
                "ep1",
                (PlanEntry("download", "下載", PlanKind.STAGE, params={"cc": "on"}),),
            )
        )
        sink.emit(
            AgentSessionFinished(
                "chunks/0001-0119", SessionOutcome.OK, 5.5, 0, {"input_tokens": 10}
            )
        )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [
        {
            "ts": "2026-10-10T12:00:00+00:00",
            "type": "RunStarted",
            "stage": None,
            "task": None,
            "project": "ep1",
            "plan": [
                {
                    "key": "download",
                    "label": "下載",
                    "kind": "stage",
                    "enabled": True,
                    "params": {"cc": "on"},
                }
            ],
        },
        {
            "ts": "2026-10-10T12:00:00+00:00",
            "type": "AgentSessionFinished",
            "stage": None,
            "task": "chunks/0001-0119",
            "outcome": "ok",
            "elapsed": 5.5,
            "repairs": 0,
            "usage": {"input_tokens": 10},
        },
    ]
    assert "下載" in lines[0]


def test_jsonl_sink_stamps_scopes_and_event_fields_win(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    with (
        JsonlSink(path) as sink,
        stage_scope("chunks"),
        task_scope("chunks/0001-0119"),
    ):
        sink.emit(StepStarted("chunks", STAGE))
        sink.emit(LogLine("INFO", "hi", stage="refine", task=None))
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [(line["stage"], line["task"]) for line in lines] == [
        ("chunks", "chunks/0001-0119"),
        ("refine", None),
    ]


def test_jsonl_sink_appends_to_an_existing_file(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    for key in ("a", "b"):
        with JsonlSink(path) as sink:
            sink.emit(StepStarted(key, STAGE))
    keys = [
        json.loads(line)["key"]
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert keys == ["a", "b"]


def test_jsonl_sink_drops_events_after_close(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    sink = JsonlSink(path)
    sink.emit(StepStarted("a", STAGE))
    sink.close()
    sink.emit(StepStarted("b", STAGE))
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_jsonl_sink_default_clock_is_utc(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    with JsonlSink(path) as sink:
        sink.emit(StepStarted("a", STAGE))
    stamp = datetime.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["ts"])
    assert stamp.utcoffset() is not None
    assert stamp.utcoffset().total_seconds() == 0  # pyright: ignore[reportOptionalMemberAccess]


def test_jsonl_sink_lines_stay_whole_under_concurrency(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    with JsonlSink(path) as sink:

        def worker(n: int) -> None:
            for i in range(100):
                sink.emit(StepStarted(f"{n}-{i}" * 20, STAGE))

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 800
    assert all(json.loads(line)["type"] == "StepStarted" for line in lines)
