"""Codex CLI (`codex exec --json`): the image-generation backend.

Facts this module is built on (codex-cli 0.160.0):

* `--ignore-user-config` keeps the user's own MCP servers and defaults out
  (auth still comes from `CODEX_HOME`); web search must be enabled
  explicitly with `-c tools.web_search=true`, done only for a task that
  requires `WEB_SEARCH`.
* `--output-schema` runs strict mode; the structured output is the last
  `agent_message` of the turn.
* `codex exec resume <thread_id>` accepts no `--cd`, so every turn runs with
  the workdir as the process cwd and resume repeats model, effort, schema,
  web search and MCP flags.
* MCP servers do not inherit the environment, so the tool-session manifest
  path travels in the server's argv.
* `error` records also report recoverable stream retries; only a turn that
  never completes is a failure.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from grillmaster.agents import process
from grillmaster.agents.adapters._jsonl import JsonlTurn, resolve_cli
from grillmaster.agents.adapters.base import (
    Capability,
    FinalOutput,
    MediaDelivery,
    TurnRequest,
)
from grillmaster.agents.errors import AgentTransientError, classify_failure
from grillmaster.agents.events import (
    Message,
    Thought,
    ToolCall,
    ToolResult,
    normalize_usage,
)
from grillmaster.core.model_spec import Backend, Effort

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence
    from pathlib import Path

    from grillmaster.agents.events import AgentEvent
    from grillmaster.core.model_spec import ModelSpec

EFFORTS = {
    Effort.LOW: "low",
    Effort.MEDIUM: "medium",
    Effort.HIGH: "high",
    Effort.EXTRA: "xhigh",
    Effort.MAX: "max",
    Effort.ULTRA: "ultra",
}
_LOGIN_HINT = "run `codex login`"
_USAGE_NAMES = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cached_input_tokens": "cached_input_tokens",
    "reasoning_output_tokens": "reasoning_tokens",
}


class CodexAdapter:
    backend = Backend.CODEX
    capabilities = frozenset(
        {
            Capability.IMAGE_INPUT,
            Capability.IMAGE_GENERATION,
            Capability.NATIVE_SCHEMA,
            Capability.RESUME,
            Capability.MCP,
            Capability.MCP_IMAGE_RESULT,
            Capability.WEB_SEARCH,
        }
    )
    media_delivery = MediaDelivery.ATTACHED

    def __init__(
        self, *, spawn: process.Spawn = process.spawn, executable: str | None = None
    ) -> None:
        self._spawn = spawn
        self._executable = executable

    def preflight(
        self,
        spec: ModelSpec,  # noqa: ARG002 - the AgentAdapter signature
        images: Sequence[Path],  # noqa: ARG002
        audio: Sequence[Path],  # noqa: ARG002
    ) -> None:
        resolve_cli("codex", self._executable)

    def start(self, request: TurnRequest) -> JsonlTurn:
        return self._turn(request, self.argv(request, thread=None))

    def resume(self, session_id: str, request: TurnRequest) -> JsonlTurn:
        return self._turn(request, self.argv(request, thread=session_id))

    def argv(self, request: TurnRequest, *, thread: str | None) -> list[str]:
        argv = [resolve_cli("codex", self._executable), "exec"]
        if thread is None:
            # `--image` takes several values; the next flag ends the list.
            for image in request.images:
                argv += ["--image", str(image)]
        else:
            argv += ["resume", thread]
        argv += [
            "--json",
            "--skip-git-repo-check",
            "--ignore-user-config",
            # The sandbox is off, so the project root needs no `--add-dir`.
            "--dangerously-bypass-approvals-and-sandbox",
            "-m",
            request.spec.model,
            "-c",
            f"model_reasoning_effort={EFFORTS[request.spec.effort]}",
        ]
        if request.web_search:
            argv += ["-c", "tools.web_search=true"]
        if thread is None:
            argv += ["--cd", str(request.workdir)]
        if request.schema_path is not None:
            argv += ["--output-schema", str(request.schema_path)]
        if request.mcp is not None:
            # Values are TOML; JSON strings and arrays are valid TOML.
            argv += [
                "-c",
                f"mcp_servers.grill.command={json.dumps(request.mcp.command)}",
                "-c",
                f"mcp_servers.grill.args={json.dumps(list(request.mcp.args))}",
            ]
        argv.append("-")  # the prompt comes on stdin
        return argv

    def _turn(self, request: TurnRequest, argv: list[str]) -> JsonlTurn:
        spec = process.ProcessSpec(
            argv=argv,
            cwd=request.workdir,
            timeout_s=request.timeout_s,
            stdin=request.message,
        )
        parser = CodexTurnParser(expects_json=request.schema is not None)
        return JsonlTurn(self._spawn(spec), parser, request)


class CodexTurnParser:
    """Reads one turn's `thread.*` / `turn.*` / `item.*` / `error` records."""

    def __init__(self, *, expects_json: bool) -> None:
        self._expects_json = expects_json
        self._session_id = ""
        self._last_message = ""
        self._announced: set[str] = set()
        self._errors: list[str] = []
        self._failure: str | None = None
        self._usage: dict[str, int] | None = None

    @property
    def done(self) -> bool:
        return self._usage is not None or self._failure is not None

    def feed(self, record: dict[str, Any]) -> Iterable[AgentEvent]:
        match record.get("type"):
            case "thread.started":
                self._session_id = str(record.get("thread_id") or "")
            case "item.started":
                yield from self._item(record.get("item") or {}, completed=False)
            case "item.completed":
                yield from self._item(record.get("item") or {}, completed=True)
            case "turn.completed":
                self._usage = normalize_usage(record.get("usage"), _USAGE_NAMES)
            case "turn.failed":
                error = record.get("error") or {}
                self._failure = str(error.get("message") or record)
            case "error":
                self._errors.append(str(record.get("message") or record))
            case _:
                pass

    def finish(self, returncode: int, stderr_tail: str) -> FinalOutput:
        if self._usage is None:
            message = self._failure or (self._errors[-1] if self._errors else None)
            if message is None:
                raise AgentTransientError(
                    f"codex exited with code {returncode} before the turn "
                    f"completed: {stderr_tail}"
                )
            raise classify_failure(message, login_hint=_LOGIN_HINT)
        return FinalOutput(
            session_id=self._session_id,
            text=self._last_message,
            structured=self._structured(),
            usage=self._usage,
        )

    def _structured(self) -> object | None:
        if not self._expects_json:
            return None
        try:
            return json.loads(self._last_message)
        except ValueError:
            return None

    def _item(self, item: dict[str, Any], *, completed: bool) -> Iterator[AgentEvent]:
        kind = item.get("type")
        item_id = str(item.get("id") or "")
        match kind:
            case "agent_message" if completed:
                text = str(item.get("text") or "")
                self._last_message = text
                if text.strip():
                    yield Message(text)
            case "reasoning" if completed:
                if text := str(item.get("text") or "").strip():
                    yield Thought(text)
            case "mcp_tool_call":
                name = str(item.get("tool") or "mcp")
                yield from self._tool(
                    item_id,
                    name,
                    item.get("arguments") or {},
                    completed=completed,
                    ok=item.get("status") == "completed" and not item.get("error"),
                )
            case "command_execution":
                yield from self._tool(
                    item_id,
                    "shell",
                    {"command": item.get("command") or ""},
                    completed=completed,
                    ok=item.get("exit_code") in (0, None)
                    and item.get("status") != "failed",
                )
            case "web_search":
                yield from self._tool(
                    item_id,
                    "web_search",
                    {"query": item.get("query") or ""},
                    completed=completed,
                    ok=True,
                )
            case "file_change" if completed:
                changes = item.get("changes") or []
                paths = [str(change.get("path")) for change in changes]
                yield from self._tool(
                    item_id, "file_change", {"paths": paths}, completed=True, ok=True
                )
            case _:
                # `error` items are warnings (e.g. unknown model metadata);
                # todo lists carry nothing worth showing.
                pass

    def _tool(
        self,
        item_id: str,
        name: str,
        args: dict[str, Any],
        *,
        completed: bool,
        ok: bool,
    ) -> Iterator[AgentEvent]:
        if item_id not in self._announced:
            self._announced.add(item_id)
            yield ToolCall(name, args)
        if completed:
            yield ToolResult(name, ok=ok)
