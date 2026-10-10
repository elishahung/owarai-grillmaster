"""`agents.process` against real child processes (a Python interpreter)."""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import time
from typing import TYPE_CHECKING

import pytest

from grillmaster.agents.process import ProcessSpec, TimeoutExpired, run_text, spawn
from grillmaster.core.process import LIVE_PROCESSES, kill_all

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.core.process import ChildProcess

_UTF8 = "import sys; sys.stdout.reconfigure(encoding='utf-8'); "


def _python(code: str, **options: object) -> ProcessSpec:
    fields: dict[str, object] = {"timeout_s": 30.0, "cwd": None}
    fields.update(options)
    return ProcessSpec(argv=[sys.executable, "-c", _UTF8 + code], **fields)  # pyright: ignore[reportArgumentType]


def test_lines_are_decoded_as_utf8():
    process = spawn(_python("print('みなみかわ'); print('{\"a\": 1}')"))
    assert list(process.lines()) == ["みなみかわ", '{"a": 1}']
    assert process.wait() == 0
    assert not process.timed_out


def test_stdin_is_written_and_closed_by_default():
    process = spawn(_python("print(sys.stdin.read().upper())", stdin="prompt"))
    assert list(process.lines()) == ["PROMPT"]


def test_stdin_can_stay_open_until_closed_explicitly():
    code = "print(sys.stdin.readline().strip(), flush=True); sys.stdin.read(); print('eof')"
    process = spawn(_python(code, stdin="first\n", keep_stdin_open=True))
    lines = process.lines()
    assert next(lines) == "first"
    process.close_stdin()
    assert list(lines) == ["eof"]


def test_stderr_tail_and_exit_code():
    process = spawn(_python("sys.stderr.write('bad\\nworse\\n'); sys.exit(3)"))
    assert list(process.lines()) == []
    assert process.wait() == 3
    assert process.stderr_tail == "bad\nworse"


def test_timeout_kills_the_whole_tree():
    # The grandchild inherits stdout; only a tree kill lets stdout reach EOF.
    code = (
        "import subprocess, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "print('started', flush=True); time.sleep(60)"
    )
    process = spawn(_python(code, timeout_s=1.0))
    began = time.monotonic()
    assert list(process.lines()) == ["started"]
    process.wait()
    assert process.timed_out
    assert time.monotonic() - began < 20


def test_run_text_collects_output_and_raises_on_timeout():
    result = run_text(_python("print('a'); print('b')"))
    assert (result.returncode, result.stdout) == (0, "a\nb")
    with pytest.raises(TimeoutExpired):
        run_text(_python("import time; time.sleep(60)", timeout_s=0.5))


def test_a_spawned_tree_is_live_until_waited_and_kill_all_ends_it():
    before = LIVE_PROCESSES.live()
    process = spawn(_python("print('up', flush=True); import time; time.sleep(60)"))
    began = time.monotonic()
    lines = process.lines()
    assert next(lines) == "up"
    fresh = [item for item in LIVE_PROCESSES.live() if item not in before]
    assert len(fresh) == 1

    kill_all()
    assert list(lines) == []
    assert process.wait() != 0
    assert time.monotonic() - began < 20
    assert LIVE_PROCESSES.live() == before


# A grandchild that holds the inherited stdout and appends to a heartbeat
# file until it is killed (or gives up after 60 s).
_GRANDCHILD = """\
import sys, time
for _ in range(1200):
    with open(sys.argv[1], "a") as beat:
        beat.write(".")
    time.sleep(0.05)
"""
# Starts the grandchild on its own stdout (the pipe), reports its pid, exits.
_PARENT = """\
import subprocess, sys
child = subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]], stdout=sys.stdout)
print(child.pid, flush=True)
"""


def _orphaning_parent(tmp_path: Path, **options: object) -> tuple[ProcessSpec, Path]:
    (tmp_path / "grandchild.py").write_text(_GRANDCHILD, encoding="utf-8")
    (tmp_path / "parent.py").write_text(_PARENT, encoding="utf-8")
    beat = tmp_path / "beat.txt"
    fields: dict[str, object] = {"timeout_s": 60.0, "cwd": None}
    fields.update(options)
    argv = [
        sys.executable,
        str(tmp_path / "parent.py"),
        str(tmp_path / "grandchild.py"),
        str(beat),
    ]
    return ProcessSpec(argv=argv, **fields), beat  # pyright: ignore[reportArgumentType]


def _assert_stopped(beat: Path) -> None:
    size = beat.stat().st_size
    time.sleep(0.5)
    assert beat.stat().st_size == size, "the grandchild is still running"


def _wait_until_exited(tree: ChildProcess) -> None:
    deadline = time.monotonic() + 10
    while tree.poll() is None:
        assert time.monotonic() < deadline, "the parent did not exit"
        time.sleep(0.05)


def _end(pid: int) -> None:
    # Cleanup after a failed assertion; on Windows `os.kill` terminates.
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGTERM)


def test_kill_all_ends_a_grandchild_whose_parent_already_exited(tmp_path: Path):
    spec, beat = _orphaning_parent(tmp_path)
    before = LIVE_PROCESSES.live()
    process = spawn(spec)
    lines = process.lines()
    grandchild = int(next(lines))
    try:
        [tree] = [item for item in LIVE_PROCESSES.live() if item not in before]
        _wait_until_exited(tree)
        began = time.monotonic()

        kill_all()
        # The reader was blocked on the grandchild's copy of stdout.
        assert list(lines) == []
        assert time.monotonic() - began < 20
        _assert_stopped(beat)
        assert LIVE_PROCESSES.live() == before
    finally:
        _end(grandchild)


def test_timeout_ends_a_grandchild_whose_parent_already_exited(tmp_path: Path):
    spec, beat = _orphaning_parent(tmp_path, timeout_s=2.0)
    process = spawn(spec)
    began = time.monotonic()
    lines = process.lines()
    grandchild = int(next(lines))
    try:
        assert list(lines) == []
        assert process.wait() == 0  # the parent's own exit code
        assert process.timed_out
        assert time.monotonic() - began < 20
        _assert_stopped(beat)
    finally:
        _end(grandchild)
