"""One CLI turn whose stdout is a JSONL event stream (agy, codex)."""

from __future__ import annotations

import contextlib
import functools
import json
import shutil
from typing import TYPE_CHECKING, Any, Protocol, override

from grillmaster.agents.adapters.base import Turn
from grillmaster.agents.errors import AgentConfigError

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable

    from grillmaster.agents.adapters.base import FinalOutput, TurnRequest
    from grillmaster.agents.events import AgentEvent
    from grillmaster.agents.process import LineProcess


class TurnParser(Protocol):
    """Backend-specific reading of one turn's records."""

    @property
    def done(self) -> bool:
        """The terminal record arrived; stdin may close."""
        ...

    def feed(self, record: dict[str, Any]) -> Iterable[AgentEvent]: ...

    def finish(self, returncode: int, stderr_tail: str) -> FinalOutput:
        """The final output, or the classified `AgentError` for this turn."""
        ...


class JsonlTurn(Turn):
    """Feeds stdout records to a parser, mirrors them to `raw`, and closes
    stdin as soon as the parser saw the terminal record (agy's stream-json
    input mode otherwise waits for another message until the timeout)."""

    def __init__(
        self, process: LineProcess, parser: TurnParser, request: TurnRequest
    ) -> None:
        super().__init__(request)
        self._process = process
        self._parser = parser
        self._returncode: int | None = None

    @override
    def events(self) -> Generator[AgentEvent]:
        if self._returncode is not None:
            return
        # Closing `lines()` ends the CLI tree, so a caller that stops this
        # generator early also stops the turn; `wait` then reaps it and
        # closes its pipes either way.
        try:
            with contextlib.closing(self._process.lines()) as lines:
                for line in lines:
                    if not line.strip():
                        continue
                    self._request.raw(line)
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue  # stray log line; kept in raw.jsonl only
                    if isinstance(record, dict):
                        yield from self._parser.feed(record)
                    if self._parser.done:
                        self._process.close_stdin()
        finally:
            self._returncode = self._process.wait()

    @property
    @override
    def timed_out(self) -> bool:
        return self._process.timed_out

    @override
    def _finish(self) -> FinalOutput:
        returncode = self._returncode if self._returncode is not None else -1
        return self._parser.finish(returncode, self._process.stderr_tail)


def resolve_cli(name: str, override: str | None) -> str:
    """`override`, else the CLI's full path from PATH (Windows shims need the
    `.cmd`); config error if absent."""
    return override if override is not None else _which(name)


@functools.cache
def _which(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise AgentConfigError(f"{name} executable not found on PATH")
    return path
