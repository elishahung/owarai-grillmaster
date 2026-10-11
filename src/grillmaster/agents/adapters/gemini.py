"""Gemini CLI (`gemini`): a subscription Gemini backend that hears attached audio.

One runner session is one `gemini --acp` process speaking the Agent Client
Protocol (`_acp.py`): `initialize` and `session/new` on the first turn, then
one `session/prompt` per turn. Facts this module is built on (gemini-cli
0.63.0):

* Why one process and not one per turn with `--resume`: a process that
  rebuilds a stored session has no real token count yet and estimates the
  history locally, counting audio by its base64 length (3.8 MB of audio
  looks like ~1.27M tokens). It then compresses the audio out of the
  history (past `model.compressionThreshold`) or, with compression off,
  refuses every later turn as overflowing the context window without
  calling the model. In one process the count after the first call is the
  API's own.
* Prompt blocks reach the model as given: text verbatim (ACP text is not
  parsed for `@` commands, unlike `--prompt`), images and audio as base64
  `image` / `audio` blocks the CLI sends as inline data. `preflight`
  refuses more than 20 MB of attachments, the Gemini API's inline request
  limit.
* Before a request with media the CLI asks `countTokens` for its size; if
  that fails (e.g. an unknown model: gemini-cli names previews
  `gemini-3.1-pro-preview`), it estimates the media by their base64 length,
  and the prompt ends with `stopReason: max_tokens` without a model call.
* Images in tool results (MCP or `read_file`) do not work through the
  Code Assist backend: gemini-3.5/3.8-flash answer HTTP 400 ("Requests
  ending with a model turn are not supported"), gemini-3.1-pro-preview and
  -flash-lite see the image but often garble the text after it, and the
  multimodal function-response format (`experimental.
  dynamicModelConfiguration`) drops the image. So frames go
  `ToolImageDelivery.NEXT_MESSAGE`: the tool returns text and the runner
  attaches the frames to the next prompt.
* There is no structured-output channel (`SchemaDelivery.PROMPT`): the
  runner states the schema in the message and reads the JSON object from
  the turn's final message, so a turn reports `text` only.
* The effort maps onto Gemini 3's `thinkingLevel` (Gemini 2.5 only takes
  a `thinkingBudget`); it, the MCP servers and `model.compressionThreshold`
  (set out of reach: a compression would summarize attached audio away, so
  an oversized session ends with `max_tokens` instead) and
  `context.includeDirectoryTree: false` (the workdir listing showed the
  chunk's `audio.ogg`; a model cut it with ffmpeg through the shell, heard
  nothing and declared its inline audio missing) go into
  `<workdir>/.gemini/settings.json`, which `session/new` reads because
  `--skip-trust` trusts the workdir. The user's own MCP servers stay off
  through `--allowed-mcp-server-names`. A tool call arrives as a
  `tool_call` update titled `<tool>(<arg>: <value>, …)`, without its raw
  name or arguments.
* A failed model call answers the prompt with a JSON-RPC error whose code
  is the HTTP status (500 when there is none). Usage comes from the prompt
  result's `_meta.quota.token_count` (input and output only).
* Paid API-key variables are removed from the environment so the CLI uses
  the subscription login, which needs `GOOGLE_CLOUD_PROJECT` for a Code
  Assist account.
"""

from __future__ import annotations

import base64
import json
import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, override

from grillmaster.agents import process
from grillmaster.agents.adapters._acp import AcpClosed, AcpConnection, AcpError
from grillmaster.agents.adapters._google import (
    MCP_SERVER,
    mcp_servers,
    subscription_env,
)
from grillmaster.agents.adapters._jsonl import resolve_cli
from grillmaster.agents.adapters.base import (
    IMAGE_MEDIA_TYPES,
    Capability,
    FinalOutput,
    MediaDelivery,
    SchemaDelivery,
    ToolImageDelivery,
    Turn,
    TurnRequest,
)
from grillmaster.agents.errors import (
    AgentConfigError,
    AgentError,
    AgentTransientError,
    classify_failure,
)
from grillmaster.agents.events import (
    Message,
    Thought,
    ToolCall,
    ToolResult,
    normalize_usage,
)
from grillmaster.core.fs import atomic_write_text
from grillmaster.core.model_spec import Backend, Effort

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator, Sequence
    from pathlib import Path

    from grillmaster.agents.adapters._acp import Notification
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
# `model.compressionThreshold` is a fraction of the context window; no
# history reaches this multiple of it, so the CLI never compresses a session.
NEVER_COMPRESS = 1000.0
SETTINGS_RELPATH = (".gemini", "settings.json")
MAX_MEDIA_BYTES = 20 * 1024 * 1024
# The audio media types the model takes, by suffix.
AUDIO_MEDIA_TYPES = {
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
}
PROTOCOL_VERSION = 1
_LOGIN_HINT = "run `gemini` once interactively to sign in"
_UNKNOWN_MODEL = "Requested entity was not found"
_NO_PROJECT = "GOOGLE_CLOUD_PROJECT"
# A tool call's title: `get_frames(times: [1, 2])`; anything else is a
# command line or a description.
_TOOL_TITLE = re.compile(r"(?s)([\w.-]+)\((.*)\)")
_TOOL_DONE = frozenset({"completed", "failed"})


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
        total = 0
        for _, path, types in _media(images, audio):
            if path.suffix.lower() not in types:
                raise AgentConfigError(
                    f"gemini takes {', '.join(sorted(types))} files: {path}"
                )
            total += path.stat().st_size
        if total > MAX_MEDIA_BYTES:
            raise AgentConfigError(
                f"gemini sends attachments inline, up to {MAX_MEDIA_BYTES // 2**20} "
                f"MB per request: {total} bytes in {[*images, *audio]}"
            )

    def session(self) -> GeminiSession:
        return GeminiSession(self)

    def argv(self, request: TurnRequest) -> list[str]:
        argv = [
            resolve_cli("gemini", self._executable),
            "--acp",
            "--model",
            request.spec.model,
            "--skip-trust",
            "--approval-mode",
            "yolo",
            "--allowed-mcp-server-names",
            MCP_SERVER,
        ]
        for root in request.add_dirs:
            argv += ["--include-directories", str(root)]
        return argv

    def connect(self, request: TurnRequest) -> AcpConnection:
        """Start the session's CLI in `request.workdir` with its settings."""
        write_settings(request)
        spec = process.ProcessSpec(
            argv=self.argv(request),
            cwd=request.workdir,
            timeout_s=request.timeout_s,
            env=subscription_env(),
            keep_stdin_open=True,
        )
        return AcpConnection(self._spawn(spec))


def write_settings(request: TurnRequest) -> None:
    """The workspace settings of a session: no workdir listing in the
    context, thinking effort, no history compression, grill MCP server."""
    effort, model = request.spec.effort, request.spec.model
    thinking = (
        {"thinkingBudget": THINKING_BUDGETS[effort]}
        if model.startswith("gemini-2.5")
        else {"thinkingLevel": THINKING_LEVELS[effort]}
    )
    model_config = {"generateContentConfig": {"thinkingConfig": thinking}}
    settings: dict[str, Any] = {
        # The workdir's file listing would show the model its own attached
        # audio as a file, and a model that then processes it with the shell
        # ends up believing the audio never reached it.
        "context": {"includeDirectoryTree": False},
        "model": {"compressionThreshold": NEVER_COMPRESS},
        "modelConfigs": {
            "overrides": [{"match": {"model": model}, "modelConfig": model_config}]
        },
    }
    if request.mcp is not None:
        settings["mcpServers"] = mcp_servers(request.mcp)
    path = request.workdir.joinpath(*SETTINGS_RELPATH)
    atomic_write_text(path, json.dumps(settings, indent=2))


class GeminiSession:
    """One `gemini --acp` process: `start` opens the ACP session
    (`initialize`, `session/new`), then each turn is a `session/prompt`."""

    def __init__(self, adapter: GeminiAdapter) -> None:
        self._adapter = adapter
        self._connection: AcpConnection | None = None
        self.session_id = ""

    def start(self, request: TurnRequest) -> GeminiTurn:
        if self._connection is not None:
            raise RuntimeError("gemini session already started")
        self._connection = connection = self._adapter.connect(request)
        try:
            connection.call(
                "initialize",
                {"protocolVersion": PROTOCOL_VERSION, "clientCapabilities": {}},
                request.raw,
            )
            # The MCP servers come from the workspace settings.
            params = {"cwd": str(request.workdir), "mcpServers": []}
            created = connection.call("session/new", params, request.raw)
        except AcpError as error:
            raise _failure(error) from error
        except AcpClosed as closed:
            raise _ended(connection, request) from closed
        session_id = created.get("sessionId") if isinstance(created, dict) else None
        if not isinstance(session_id, str) or not session_id:
            raise AgentTransientError(f"gemini opened no session: {created}")
        self.session_id = session_id
        return GeminiTurn(self, connection, request)

    def resume(self, session_id: str, request: TurnRequest) -> GeminiTurn:
        if self._connection is None or session_id != self.session_id:
            raise RuntimeError(f"gemini session {session_id!r} is not this one")
        return GeminiTurn(self, self._connection, request)

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()


class GeminiTurn(Turn):
    """One `session/prompt`, its `session/update` notifications read as
    events."""

    def __init__(
        self, session: GeminiSession, connection: AcpConnection, request: TurnRequest
    ) -> None:
        super().__init__(request)
        self._session = session
        self._connection = connection
        self._updates = UpdateReader(session.session_id)
        self._result: dict[str, Any] | None = None
        self._error: AgentError | None = None
        self._done = False

    @override
    def events(self) -> Generator[AgentEvent]:
        if self._done:
            return
        process = self._connection.process
        process.restart_watchdog(self._request.timeout_s)
        try:
            try:
                prompt = self._prompt()
            except OSError as error:
                self._error = AgentTransientError(
                    f"gemini attachment unreadable: {error}"
                )
                return
            # Sent, then dropped: the inline media are not held while the
            # turn runs.
            prompt_id = self._connection.request("session/prompt", prompt)
            del prompt
            responses = self._connection.response(prompt_id, self._request.raw)
            while True:
                try:
                    notification = next(responses)
                except StopIteration as answered:
                    result = answered.value
                    break
                yield from self._updates.feed(notification)
            self._result = result if isinstance(result, dict) else {}
            yield from self._updates.flush()
        except AcpError as error:
            self._error = _failure(error)
        except AcpClosed:
            self._error = _ended(self._connection, self._request)
        finally:
            if self._result is None and self._error is None:
                # Stopped early: the session cannot take another turn.
                self._session.close()
            process.restart_watchdog(None)
            self._done = True

    @property
    @override
    def timed_out(self) -> bool:
        # A watchdog firing after the answer arrived does not undo it.
        return self._result is None and self._connection.process.timed_out

    @override
    def _finish(self) -> FinalOutput:
        if self._error is not None:
            raise self._error
        result = self._result or {}
        stop = result.get("stopReason")
        if stop == "max_tokens":
            raise AgentConfigError(
                "gemini refused the request as larger than the context window "
                "without calling the model (an unknown model id makes its "
                "token check count attached media by their size)"
            )
        if stop != "end_turn":
            raise AgentTransientError(f"gemini stopped the turn: {stop}")
        meta = result.get("_meta")
        quota = meta.get("quota") if isinstance(meta, dict) else None
        counts = quota.get("token_count") if isinstance(quota, dict) else None
        usage = normalize_usage(
            counts, {"input_tokens": "input_tokens", "output_tokens": "output_tokens"}
        )
        return FinalOutput(
            session_id=self._session.session_id,
            text=self._updates.last_text,
            # `SchemaDelivery.PROMPT`: the runner reads the answer from `text`.
            structured=None,
            usage=usage,
        )

    def _prompt(self) -> dict[str, Any]:
        """The message text, then one inline block per attached file."""
        request = self._request
        blocks: list[dict[str, str]] = [{"type": "text", "text": request.message}]
        for kind, path, types in _media(request.images, request.audio):
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            mime = types[path.suffix.lower()]
            blocks.append({"type": kind, "mimeType": mime, "data": data})
        return {"sessionId": self._session.session_id, "prompt": blocks}


class UpdateReader:
    """Reads one turn's `session/update` notifications: message chunks
    joined into messages split at tool calls, thoughts, tool calls."""

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._pending: list[str] = []
        self.last_text = ""
        self._tools: dict[str, str] = {}

    def feed(self, notification: Notification) -> Iterator[AgentEvent]:
        method, params = notification
        update = params.get("update")
        if (
            method != "session/update"
            or params.get("sessionId") != self._session_id
            or not isinstance(update, dict)
        ):
            return
        kind = update.get("sessionUpdate")
        content = update.get("content")
        text = content.get("text") if isinstance(content, dict) else None
        tool_id = str(update.get("toolCallId") or "")
        match kind:
            case "agent_message_chunk" if isinstance(text, str):
                self._pending.append(text)
            case "agent_thought_chunk" if isinstance(text, str):
                # `**<subject>**\n<text>`; a continuation has no subject.
                thought = text.removeprefix("****").strip()
                if thought:
                    yield Thought(thought)
            case "tool_call":
                yield from self.flush()
                name, args = _tool_title(str(update.get("title") or ""))
                self._tools[tool_id] = name
                yield ToolCall(name, args)
            case _:
                pass
        status = update.get("status")
        if kind in {"tool_call", "tool_call_update"} and status in _TOOL_DONE:
            yield ToolResult(self._tools.get(tool_id, "tool"), ok=status == "completed")

    def flush(self) -> Iterator[AgentEvent]:
        """The assistant text since the last tool call, as one message."""
        text = "".join(self._pending).strip()
        self._pending.clear()
        if text:
            self.last_text = text
            yield Message(text)


def _media(
    images: Sequence[Path], audio: Sequence[Path]
) -> Iterator[tuple[str, Path, dict[str, str]]]:
    """Each attached file with its ACP block type and that block's media
    types by suffix."""
    for path in images:
        yield "image", path, IMAGE_MEDIA_TYPES
    for path in audio:
        yield "audio", path, AUDIO_MEDIA_TYPES


def _ended(connection: AcpConnection, request: TurnRequest) -> AgentTransientError:
    """The error for a CLI whose stdout ended before its answer."""
    code = connection.close()
    if connection.process.timed_out:
        return AgentTransientError(
            f"gemini turn timed out after {request.timeout_s:g}s"
        )
    return AgentTransientError(
        f"gemini exited with code {code} without a result: "
        f"{connection.process.stderr_tail}"
    )


def _tool_title(title: str) -> tuple[str, dict[str, object]]:
    """A tool call's name and its arguments as shown, from its title."""
    match = _TOOL_TITLE.fullmatch(title.strip())
    if match is None:
        return title.strip() or "tool", {}
    name, args = match.groups()
    return name, {"args": args} if args else {}


def _failure(error: AcpError) -> AgentError:
    message = str(error)
    if _NO_PROJECT in message:
        return AgentConfigError(
            f"gemini needs {_NO_PROJECT} for this account: {message}"
        )
    if _UNKNOWN_MODEL in message:
        return AgentConfigError(f"gemini has no such model: {message}")
    # 500 is the CLI's code for an error without a status.
    status = (
        error.code
        if HTTPStatus.BAD_REQUEST <= error.code < HTTPStatus.INTERNAL_SERVER_ERROR
        else None
    )
    return classify_failure(message, login_hint=_LOGIN_HINT, status=status)
