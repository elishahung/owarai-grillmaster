"""`core.process`: the registry with fake processes, the abort latch and
adoption; the real tree kill is covered by the agents and media spawn tests."""

from __future__ import annotations

import subprocess
import sys

import pytest

from grillmaster.core.process import (
    LIVE_PROCESSES,
    ProcessAbortedError,
    ProcessRegistry,
    adopt_tree,
    kill_all,
    spawn_tree,
    track,
)


class _Fake:
    def __init__(self, name: str) -> None:
        self.name = name


def test_registry_kills_every_live_process_until_unregistered():
    killed: list[str] = []
    registry = ProcessRegistry[_Fake](lambda process: killed.append(process.name))
    first, second = _Fake("first"), _Fake("second")
    registry.register(first)
    registry.register(second)

    assert registry.kill_all() == 2
    assert killed == ["first", "second"]
    # Killing does not unregister: the owner does, once the tree is dead.
    assert registry.live() == [first, second]

    registry.unregister(first)
    registry.unregister(first)  # twice is fine
    assert registry.kill_all() == 1
    assert killed == ["first", "second", "second"]


def test_one_unkillable_process_does_not_spare_the_rest():
    killed: list[str] = []

    def kill(process: _Fake) -> None:
        if process.name == "stuck":
            raise PermissionError("access denied")
        killed.append(process.name)

    registry = ProcessRegistry[_Fake](kill)
    registry.register(_Fake("stuck"))
    registry.register(_Fake("other"))
    assert registry.kill_all() == 2
    assert killed == ["other"]


def test_kill_all_closes_the_registry_for_good():
    registry = ProcessRegistry[_Fake](lambda process: None)
    registry.kill_all()
    assert registry.abort.is_set()
    with pytest.raises(ProcessAbortedError):
        registry.register(_Fake("late"))
    assert registry.live() == []


def test_a_tree_spawned_after_an_abort_is_ended_not_tracked():
    tree = spawn_tree(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=None,
        env=None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    kill_all()
    with pytest.raises(ProcessAbortedError):
        track(tree)
    assert tree.leader.wait(timeout=10) != 0
    assert tree not in LIVE_PROCESSES.live()


@pytest.mark.skipif(sys.platform != "win32", reason="Job Objects are Windows-only")
def test_adopting_a_process_that_cannot_join_a_job_fails_loudly():
    exited = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: TID251
    exited.wait()
    with pytest.raises(OSError):  # noqa: PT011 - any Win32 error will do
        adopt_tree(exited)
