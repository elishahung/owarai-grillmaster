"""A client for the Agent Client Protocol: JSON-RPC 2.0, one message per line,
over a CLI's stdin and stdout (gemini's `--acp` mode).

Single-threaded and pull-driven: `request` sends, `response` reads stdout
until that request's answer, yielding the notifications that arrive
meanwhile and answering the agent's own requests on the way. Sending and
waiting are apart so a large request (inline audio) is not held while the
agent works. The connection outlives requests: one CLI process serves a
whole session.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from grillmaster.agents.adapters._jsonl import read_record

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from grillmaster.agents.process import LineProcess

# JSON-RPC's "method not found".
_NO_SUCH_METHOD = -32601


class AcpError(Exception):
    """The agent answered a request with a JSON-RPC error."""

    def __init__(self, code: int, message: str, data: object = None) -> None:
        super().__init__(message if data is None else f"{message} ({data})")
        self.code = code


class AcpClosed(Exception):  # noqa: N818 - an event, not a bug
    """Stdout ended before the response arrived: the CLI exited or was killed."""


type Notification = tuple[str, dict[str, Any]]


class AcpConnection:
    def __init__(self, process: LineProcess) -> None:
        self.process = process
        self._lines = process.lines()
        self._last_id = 0
        self._returncode: int | None = None

    def request(self, method: str, params: dict[str, Any]) -> int:
        """Send `method`; its request id."""
        self._last_id += 1
        self._send({"id": self._last_id, "method": method, "params": params})
        return self._last_id

    def response(
        self, request_id: int, raw: Callable[[str], None]
    ) -> Generator[Notification, None, Any]:
        """Yield each notification (method, params) until the response to
        `request_id`, and return its result. Every stdout line goes to `raw`.
        Raises `AcpError` for an error response, `AcpClosed` at EOF."""
        for line in self._lines:
            message = read_record(line, raw)
            if message is None:
                continue
            if "method" in message:
                params = message.get("params")
                params = params if isinstance(params, dict) else {}
                if "id" in message:
                    self._answer(message["id"], str(message["method"]), params)
                else:
                    yield str(message["method"]), params
            elif message.get("id") == request_id:
                error = message.get("error")
                if isinstance(error, dict):
                    raise AcpError(
                        int(error.get("code") or 0),
                        str(error.get("message") or error),
                        error.get("data"),
                    )
                return message.get("result")
        raise AcpClosed

    def call(
        self, method: str, params: dict[str, Any], raw: Callable[[str], None]
    ) -> Any:
        """`request` and its result; notifications meanwhile are dropped."""
        responses = self.response(self.request(method, params), raw)
        while True:
            try:
                next(responses)
            except StopIteration as done:
                return done.value

    def close(self) -> int:
        """End the CLI (its tree killed, then stdin closed); its exit code.
        Idempotent."""
        if self._returncode is None:
            self._lines.close()
            self._returncode = self.process.wait()
        return self._returncode

    def _answer(self, request_id: object, method: str, params: dict[str, Any]) -> None:
        """Approve a permission request (the CLI runs in yolo mode, so tools
        normally ask nothing); refuse anything else, as no client capability
        was offered."""
        if method == "session/request_permission":
            options = params.get("options")
            allow = next(
                (
                    option.get("optionId")
                    for option in options or ()
                    if isinstance(option, dict)
                    and str(option.get("kind", "")).startswith("allow")
                ),
                None,
            )
            outcome = (
                {"outcome": "selected", "optionId": allow}
                if allow is not None
                else {"outcome": "cancelled"}
            )
            self._send({"id": request_id, "result": {"outcome": outcome}})
            return
        error = {"code": _NO_SUCH_METHOD, "message": f"unsupported: {method}"}
        self._send({"id": request_id, "error": error})

    def _send(self, message: dict[str, Any]) -> None:
        payload = {"jsonrpc": "2.0", **message}
        self.process.send(json.dumps(payload, ensure_ascii=False) + "\n")
