"""`core.process.ProcessRegistry` with fake processes; the real tree kill is
covered by the agents and media spawn tests."""

from __future__ import annotations

from grillmaster.core.process import ProcessRegistry


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
