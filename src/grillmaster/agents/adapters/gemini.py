"""Gemini CLI (`gemini`): a subscription Gemini backend that hears attached audio.

One turn is `gemini … --output-format stream-json --prompt=` with the user
message on stdin. Facts this module is built on (gemini-cli 0.63.0):

* Media reach the model as `@"<absolute path>"` tokens (quoted, so a path
  may hold spaces) the CLI inlines into the user turn, so the adapter
  appends one per image and audio file and adds each parent folder with
  `--include-directories` (the CLI refuses paths outside its workspace).
  The CLI drops files over 20 MB; `preflight` refuses them.
* The CLI parses every `@` of the message, not just ours: an `@name` may be
  resolved to a workspace file, a subagent or an MCP resource, and once a
  file attaches, the text around each `@` is respaced (newlines between
  them dropped). Chat mentions and e-mail addresses would be mangled, so
  the message's own `@` become the fullwidth U+FF20, which the parser skips.
* Images in tool results (MCP or `read_file`) do not work through the
  Code Assist backend: gemini-3.5/3.8-flash answer HTTP 400 ("Requests
  ending with a model turn are not supported"), gemini-3.1-pro-preview and
  -flash-lite see the image but often garble the text after it, and the
  multimodal function-response format (`experimental.
  dynamicModelConfiguration`) drops the image. So frames go
  `ToolImageDelivery.NEXT_MESSAGE`: the tool returns text and the runner
  attaches the frames, as `@` tokens, to the next message.
* There is no structured-output flag (`SchemaDelivery.PROMPT`): the runner
  states the schema in the message and reads the JSON object from the
  turn's final message, so the parser reports `text` only.
* The effort maps onto Gemini 3's `thinkingLevel` (Gemini 2.5 only takes
  a `thinkingBudget`) and MCP servers are
  configured through `<workdir>/.gemini/settings.json` (workspace settings,
  read because `--skip-trust` trusts the workdir); the user's own MCP
  servers stay off through `--allowed-mcp-server-names`, and their calls
  appear as `mcp_<server>_<tool>`.
* `--resume <session id>` continues a session stored per cwd, so the
  workdir must stay the same.
* Paid API-key variables are removed from the environment so the CLI uses
  the subscription login, which needs `GOOGLE_CLOUD_PROJECT` for a Code
  Assist account.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from grillmaster.agents import process
from grillmaster.agents.adapters._google import (
    MCP_SERVER,
    mcp_servers,
    readable_roots,
    subscription_env,
)
from grillmaster.agents.adapters._jsonl import JsonlTurn, resolve_cli
from grillmaster.agents.adapters.base import (
    Capability,
    FinalOutput,
    MediaDelivery,
    SchemaDelivery,
    ToolImageDelivery,
    TurnRequest,
)
from grillmaster.agents.errors import (
    AgentConfigError,
    AgentError,
    AgentTransientError,
    classify_failure,
)
from grillmaster.agents.events import Message, ToolCall, ToolResult, normalize_usage
from grillmaster.core.fs import atomic_write_text
from grillmaster.core.model_spec import Backend, Effort

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence
    from pathlib import Path

    from grillmaster.agents.events import AgentEvent
    from grillmaster.core.model_spec import ModelSpec

# Gemini 3 thinking levels; the higher repo efforts clamp to HIGH.
THINKING_LEVELS = {
    Effort.LOW: "LOW",
    Effort.MEDIUM: "MEDIUM",
    Effort.HIGH: "HIGH",
    Effort.EXTRA: "HIGH",
    Effort.MAX: "HIGH",
    Effort.ULTRA: "HIGH",
}
# Gemini 2.5 token budgets (within both 2.5 Pro's and Flash's limits).
THINKING_BUDGETS = {
    Effort.LOW: 1024,
    Effort.MEDIUM: 8192,
    Effort.HIGH: 24576,
    Effort.EXTRA: 24576,
    Effort.MAX: 24576,
    Effort.ULTRA: 24576,
}
# The message's own `@`, which the CLI would parse as `@path` commands.
LITERAL_AT = "\N{FULLWIDTH COMMERCIAL AT}"
SETTINGS_RELPATH = (".gemini", "settings.json")
MAX_MEDIA_BYTES = 20 * 1024 * 1024
_MCP_TOOL_PREFIX = f"mcp_{MCP_SERVER}_"
_LOGIN_HINT = "run `gemini` once interactively to sign in"
_UNKNOWN_MODEL = "Requested entity was not found"
_NO_PROJECT = "GOOGLE_CLOUD_PROJECT"
_USAGE_NAMES = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cached": "cached_input_tokens",
}


class GeminiAdapter:
    backend = Backend.GEMINI
    capabilities = frozenset(
        {
            Capability.AUDIO_INPUT,
            Capability.IMAGE_INPUT,
            Capability.SCHEMA_OUTPUT,
            Capability.RESUME,
            Capability.MCP,
            Capability.WEB_SEARCH,
        }
    )
    media_delivery = MediaDelivery.ATTACHED
    tool_image_delivery = ToolImageDelivery.NEXT_MESSAGE
    schema_delivery = SchemaDelivery.PROMPT

    def __init__(
        self, *, spawn: process.Spawn = process.spawn, executable: str | None = None
    ) -> None:
        self._spawn = spawn
        self._executable = executable

    def preflight(
        self,
        spec: ModelSpec,  # noqa: ARG002 - the AgentAdapter signature
        images: Sequence[Path],
        audio: Sequence[Path],
    ) -> None:
        resolve_cli("gemini", self._executable)
        for path in (*images, *audio):
            if path.stat().st_size > MAX_MEDIA_BYTES:
                raise AgentConfigError(
                    f"gemini attaches files up to {MAX_MEDIA_BYTES // 2**20} MB: {path}"
                )

    def start(self, request: TurnRequest) -> JsonlTurn:
        return self._turn(request, session=None)

    def resume(self, session_id: str, request: TurnRequest) -> JsonlTurn:
        return self._turn(request, session=session_id)

    def argv(self, request: TurnRequest, *, session: str | None) -> list[str]:
        argv = [
            resolve_cli("gemini", self._executable),
            "--model",
            request.spec.model,
            "--output-format",
            "stream-json",
            "--skip-trust",
            "--approval-mode",
            "yolo",
            "--allowed-mcp-server-names",
            MCP_SERVER,
        ]
        for root in _include_dirs(request):
            argv += ["--include-directories", str(root)]
        if session is not None:
            argv += ["--resume", session]
        # Headless; the prompt itself comes on stdin.
        argv.append("--prompt=")
        return argv

    def _turn(self, request: TurnRequest, *, session: str | None) -> JsonlTurn:
        argv = self.argv(request, session=session)
        write_settings(request)
        spec = process.ProcessSpec(
            argv=argv,
            cwd=request.workdir,
            timeout_s=request.timeout_s,
            env=subscription_env(),
            stdin=_message(request),
        )
        parser = GeminiTurnParser()
        return JsonlTurn(self._spawn(spec), parser, request)


def write_settings(request: TurnRequest) -> None:
    """The workspace settings of this turn: thinking effort, grill MCP server."""
    effort, model = request.spec.effort, request.spec.model
    thinking = (
        {"thinkingBudget": THINKING_BUDGETS[effort]}
        if model.startswith("gemini-2.5")
        else {"thinkingLevel": THINKING_LEVELS[effort]}
    )
    model_config = {"generateContentConfig": {"thinkingConfig": thinking}}
    settings: dict[str, Any] = {
        "modelConfigs": {
            "overrides": [{"match": {"model": model}, "modelConfig": model_config}]
        }
    }
    if request.mcp is not None:
        settings["mcpServers"] = mcp_servers(request.mcp)
    path = request.workdir.joinpath(*SETTINGS_RELPATH)
    atomic_write_text(path, json.dumps(settings, indent=2))


class GeminiTurnParser:
    """Reads one turn's `init` / `message` / `tool_use` / `tool_result` /
    `result` records."""

    def __init__(self) -> None:
        self._session_id = ""
        # Assistant deltas since the last tool call.
        self._pending: list[str] = []
        self._last_text = ""
        self._tools: dict[str, str] = {}
        self._result: dict[str, Any] | None = None

    @property
    def done(self) -> bool:
        return self._result is not None

    def feed(self, record: dict[str, Any]) -> Iterable[AgentEvent]:
        match record.get("type"):
            case "init":
                self._session_id = str(record.get("session_id") or "")
            case "message" if record.get("role") == "assistant":
                self._pending.append(str(record.get("content") or ""))
            case "tool_use":
                yield from self._flush()
                tool_id = str(record.get("tool_id") or "")
                tool_name = str(record.get("tool_name") or "")
                name = tool_name.removeprefix(_MCP_TOOL_PREFIX)
                self._tools[tool_id] = name
                yield ToolCall(name, record.get("parameters") or {})
            case "tool_result":
                name = self._tools.get(str(record.get("tool_id") or ""), "tool")
                yield ToolResult(name, ok=record.get("status") == "success")
            case "result":
                yield from self._flush()
                self._result = record
            case _:
                pass

    def finish(self, returncode: int, stderr_tail: str) -> FinalOutput:
        result = self._result
        if result is None:
            if _NO_PROJECT in stderr_tail:
                raise AgentConfigError(
                    f"gemini needs {_NO_PROJECT} for this account: {stderr_tail}"
                )
            raise AgentTransientError(
                f"gemini exited with code {returncode} without a result: {stderr_tail}"
            )
        if result.get("status") != "success":
            raise _failure(result.get("error"))
        return FinalOutput(
            session_id=self._session_id,
            text=self._last_text,
            # `SchemaDelivery.PROMPT`: the runner reads the answer from `text`.
            structured=None,
            usage=_usage(result.get("stats")),
        )

    def _flush(self) -> Iterator[AgentEvent]:
        """The assistant text since the last tool call, as one message."""
        text = "".join(self._pending).strip()
        self._pending.clear()
        if text:
            self._last_text = text
            yield Message(text)


def _failure(error: object) -> AgentError:
    message = (
        str(error.get("message") or error) if isinstance(error, dict) else str(error)
    )
    if _UNKNOWN_MODEL in message:
        return AgentConfigError(f"gemini has no such model: {message}")
    return classify_failure(message, login_hint=_LOGIN_HINT)


def _usage(stats: object) -> dict[str, int]:
    usage = normalize_usage(stats, _USAGE_NAMES)
    # Thought tokens are the part of the total that is neither input nor output.
    if isinstance(stats, dict) and isinstance(total := stats.get("total_tokens"), int):
        thoughts = total - usage.get("input_tokens", 0) - usage.get("output_tokens", 0)
        if thoughts > 0:
            usage["reasoning_tokens"] = thoughts
    return usage


def _message(request: TurnRequest) -> str:
    """The user message with its own `@` made literal, then one quoted
    `@"path"` token per attached file."""
    text = request.message.replace("@", LITERAL_AT)
    tokens = "\n".join(f'@"{path}"' for path in (*request.images, *request.audio))
    return f"{text}\n\n{tokens}" if tokens else text


def _include_dirs(request: TurnRequest) -> list[Path]:
    """Readable roots beyond the workdir (the CLI's own workspace)."""
    return readable_roots(request)[1:]
