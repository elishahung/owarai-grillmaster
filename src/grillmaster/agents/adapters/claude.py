"""Claude Code through `claude-agent-sdk`: a synchronous adapter over the async SDK.

Each turn runs the SDK's event loop on its own thread (the pipeline is
thread-based; this is the only module allowed `asyncio.run`) and hands
messages over a queue. Facts this module is built on (SDK 0.2.135, verified
live with the bundled CLI):

* `setting_sources=[]` + `strict_mcp_config=True` keep the user's settings,
  CLAUDE.md files and MCP servers out of the session.
* `output_format` makes the model answer through a `StructuredOutput` tool;
  the value arrives as `ResultMessage.structured_output`.
* MCP tools are named `mcp__<server>__<tool>` and image results reach the
  model; `resume=<session_id>` keeps the context, tools and schema.
* A 429 surfaces as a rejected `RateLimitEvent` / error result, after which
  the SDK raises an opaque exception; the captured details win.

Raw records are the SDK message dataclasses encoded by `encode_message`
(`thinking_tokens` progress ticks are dropped before they leave the pump).

The SDK spawns the CLI itself, so each turn hands `query()` a
`RegisteredTransport`: the SDK's own subprocess transport, whose child is held
in `core.process.LIVE_PROCESSES` from `connect` to `close`, so an abort's
`kill_all` tree-kills it like any other agent process.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import json
import queue
import threading
from collections import deque
from typing import TYPE_CHECKING, Any, override

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    CLINotFoundError,
    RateLimitEvent,
    ResultMessage,
    ServerToolUseBlock,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)
from claude_agent_sdk._internal.transport.subprocess_cli import (
    SubprocessCLITransport,
)

from grillmaster.agents.adapters.base import (
    Capability,
    FinalOutput,
    MediaDelivery,
    Turn,
    TurnRequest,
)
from grillmaster.agents.errors import (
    AgentAuthError,
    AgentConfigError,
    AgentError,
    AgentQuotaError,
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
from grillmaster.core.model_spec import Backend, Effort
from grillmaster.core.process import LIVE_PROCESSES

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator, Sequence
    from pathlib import Path

    from anyio.abc import Process
    from claude_agent_sdk.types import EffortLevel

    from grillmaster.agents.events import AgentEvent
    from grillmaster.core.model_spec import ModelSpec

type QueryFn = Callable[..., AsyncIterator[Any]]
type Prompt = str | AsyncIterator[dict[str, Any]]

EFFORTS: dict[Effort, EffortLevel] = {
    Effort.LOW: "low",
    Effort.MEDIUM: "medium",
    Effort.HIGH: "high",
    Effort.EXTRA: "xhigh",
    Effort.MAX: "max",
    # Claude has no level above max.
    Effort.ULTRA: "max",
}
STRUCTURED_OUTPUT_TOOL = "StructuredOutput"
_LOGIN_HINT = "run `claude auth login --claudeai`"
_IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_USAGE_NAMES = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_input_tokens": "cached_input_tokens",
}
_STDERR_TAIL_LINES = 40


class ClaudeAdapter:
    backend = Backend.CLAUDE
    capabilities = frozenset(
        {
            Capability.IMAGE_INPUT,
            Capability.NATIVE_SCHEMA,
            Capability.RESUME,
            Capability.MCP,
            Capability.MCP_IMAGE_RESULT,
            Capability.WEB_SEARCH,
        }
    )
    media_delivery = MediaDelivery.ATTACHED

    def __init__(self, *, query_fn: QueryFn = query) -> None:
        self._query = query_fn

    def preflight(
        self,
        spec: ModelSpec,  # noqa: ARG002 - the AgentAdapter signature
        images: Sequence[Path],
        audio: Sequence[Path],  # noqa: ARG002
    ) -> None:
        for path in images:
            _media_type(path)

    def start(self, request: TurnRequest) -> ClaudeTurn:
        return ClaudeTurn(self._query, request, resume=None)

    def resume(self, session_id: str, request: TurnRequest) -> ClaudeTurn:
        return ClaudeTurn(self._query, request, resume=session_id)


def build_options(
    request: TurnRequest,
    *,
    resume: str | None,
    stderr: Callable[[str], None] | None = None,
) -> ClaudeAgentOptions:
    mcp_servers: dict[str, Any] = {}
    if request.mcp is not None:
        mcp_servers["grill"] = {
            "type": "stdio",
            "command": request.mcp.command,
            "args": list(request.mcp.args),
        }
    return ClaudeAgentOptions(
        cwd=request.workdir,
        model=request.spec.model,
        effort=EFFORTS[request.spec.effort],
        permission_mode="bypassPermissions",
        setting_sources=[],
        strict_mcp_config=True,
        mcp_servers=mcp_servers,
        output_format=(
            {"type": "json_schema", "schema": request.schema}
            if request.schema is not None
            else None
        ),
        add_dirs=list(request.add_dirs),
        resume=resume,
        stderr=stderr,
    )


class ClaudeTurn(Turn):
    """One `query()` call pumped on a worker thread."""

    _END = object()

    def __init__(
        self, query_fn: QueryFn, request: TurnRequest, *, resume: str | None
    ) -> None:
        super().__init__(request)
        # The SDK hands stderr over line by line through a callback.
        self._stderr: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
        self._options = build_options(
            request, resume=resume, stderr=self._stderr.append
        )
        self._parser = ClaudeTurnParser()
        self._queue: queue.Queue[object] = queue.Queue()
        self._failure: BaseException | None = None
        self._timed_out = False
        self._finished = False
        self._thread = threading.Thread(
            target=self._pump, args=(query_fn, _prompt(request)), daemon=True
        )
        self._thread.start()

    @override
    def events(self) -> Iterator[AgentEvent]:
        while not self._finished:
            item = self._queue.get()
            if item is self._END:
                self._finished = True
                break
            self._request.raw(json.dumps(encode_message(item), ensure_ascii=False))
            yield from self._parser.feed(item)
        self._thread.join()

    @property
    @override
    def timed_out(self) -> bool:
        return self._timed_out

    @override
    def _finish(self) -> FinalOutput:
        return self._parser.finish(self._failure, "\n".join(self._stderr))

    def _pump(self, query_fn: QueryFn, prompt: Prompt) -> None:
        async def consume() -> None:
            transport = RegisteredTransport(prompt=prompt, options=self._options)
            async with asyncio.timeout(self._request.timeout_s):
                async for message in query_fn(
                    prompt=prompt, options=self._options, transport=transport
                ):
                    if not is_progress_tick(message):
                        self._queue.put(message)

        try:
            asyncio.run(consume())
        except TimeoutError:
            self._timed_out = True
        except Exception as error:  # noqa: BLE001 - classified in `finish`
            self._failure = error
        finally:
            self._queue.put(self._END)


class RegisteredTransport(SubprocessCLITransport):
    """The SDK's CLI transport with its child in `LIVE_PROCESSES` while
    connected. Relies on the SDK's private `_process` (anyio `Process`)."""

    _handle: CliProcess | None = None

    @override
    async def connect(self) -> None:
        await super().connect()
        process = self._process
        if process is not None and self._handle is None:
            self._handle = CliProcess(process)
            LIVE_PROCESSES.register(self._handle)

    @override
    async def close(self) -> None:
        try:
            await super().close()
        finally:
            if self._handle is not None:
                LIVE_PROCESSES.unregister(self._handle)
                self._handle = None


class CliProcess:
    """An anyio `Process` as a `core.process.ChildProcess`."""

    def __init__(self, process: Process) -> None:
        self._process = process

    @property
    def pid(self) -> int:
        return self._process.pid

    def poll(self) -> int | None:
        return self._process.returncode

    def kill(self) -> None:
        self._process.kill()


class ClaudeTurnParser:
    """Normalizes SDK messages and classifies the turn's outcome."""

    def __init__(self) -> None:
        self._session_id = ""
        self._last_text = ""
        self._tool_names: dict[str, str] = {}
        self._rate_limit: str | None = None
        self._assistant_error: tuple[str, str] | None = None
        self._result: ResultMessage | None = None

    def feed(self, message: object) -> Iterator[AgentEvent]:
        match message:
            case SystemMessage(subtype="init", data=data):
                self._session_id = str(data.get("session_id") or "")
            case AssistantMessage(error=error, content=content) if error:
                text = "".join(b.text for b in content if isinstance(b, TextBlock))
                if error == "rate_limit":
                    self._rate_limit = text or "rate limit reached"
                else:
                    self._assistant_error = (error, text)
            case AssistantMessage(content=content):
                yield from self._assistant_blocks(content)
            case UserMessage(content=list() as content):
                for block in content:
                    if isinstance(block, ToolResultBlock) and (
                        name := self._tool_names.get(block.tool_use_id)
                    ):
                        yield ToolResult(name, ok=not block.is_error)
            case RateLimitEvent(rate_limit_info=info) if info.status == "rejected":
                self._rate_limit = self._rate_limit or (
                    f"{info.rate_limit_type or 'rate'} limit reached"
                    + (f", resets at {info.resets_at}" if info.resets_at else "")
                )
            case ResultMessage() as result:
                self._result = result
            case _:
                pass

    def finish(self, failure: BaseException | None, stderr_tail: str) -> FinalOutput:
        """The final output, or the classified `AgentError` for this turn;
        `failure` is what the SDK raised, if anything."""
        result = self._result
        if self._rate_limit is not None:
            raise AgentQuotaError(f"Claude rate limit hit: {self._rate_limit}")
        if result is not None and result.is_error:
            raise _classify_result(result, self._assistant_error)
        if self._assistant_error is not None:
            code, text = self._assistant_error
            raise _classify_code(code, text)
        if failure is not None:
            if isinstance(failure, CLINotFoundError):
                raise AgentConfigError(f"Claude Code CLI not found: {failure}")
            raise AgentTransientError(
                f"claude-agent-sdk failed: {failure}; stderr: {stderr_tail}"
            )
        if result is None:
            raise AgentTransientError(
                f"claude ended without a result; stderr: {stderr_tail}"
            )
        return FinalOutput(
            session_id=result.session_id or self._session_id,
            text=result.result or self._last_text,
            structured=result.structured_output,
            usage=normalize_usage(result.usage, _USAGE_NAMES),
        )

    def _assistant_blocks(self, content: list[Any]) -> Iterator[AgentEvent]:
        for block in content:
            match block:
                case ThinkingBlock(thinking=thinking):
                    yield Thought(thinking)
                case TextBlock(text=text):
                    self._last_text = text
                    yield Message(text)
                case ToolUseBlock(name=name) if name == STRUCTURED_OUTPUT_TOOL:
                    pass  # the final answer itself, reported by the result
                case (
                    ToolUseBlock(id=tool_id, name=name, input=args)
                    | ServerToolUseBlock(id=tool_id, name=name, input=args)
                ):
                    short = tool_name(name)
                    self._tool_names[tool_id] = short
                    yield ToolCall(short, args)
                case _:
                    pass


def tool_name(name: str) -> str:
    """`mcp__grill__get_frames` -> `get_frames`; built-in names pass through."""
    if name.startswith("mcp__"):
        return name.split("__", 2)[-1]
    return name


def is_progress_tick(message: object) -> bool:
    """Per-token `thinking_tokens` estimates: noise in raw logs."""
    return isinstance(message, SystemMessage) and message.subtype == "thinking_tokens"


def encode_message(value: object) -> Any:
    """An SDK message as JSON data: dataclasses become `{"_type": name, ...}`."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "_type": type(value).__name__,
            **{
                item.name: encode_message(getattr(value, item.name))
                for item in dataclasses.fields(value)
            },
        }
    if isinstance(value, dict):
        return {str(key): encode_message(item) for key, item in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, (list, tuple)):
        return [encode_message(item) for item in value]  # pyright: ignore[reportUnknownVariableType]
    return value


def _prompt(request: TurnRequest) -> Prompt:
    """A plain string, or one streamed user message carrying image blocks."""
    if not request.images:
        return request.message
    content: list[dict[str, Any]] = [{"type": "text", "text": request.message}]
    content += [_image_block(path) for path in request.images]
    message = {"type": "user", "message": {"role": "user", "content": content}}

    async def stream() -> AsyncIterator[dict[str, Any]]:
        yield message

    return stream()


def _image_block(path: Path) -> dict[str, Any]:
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": _media_type(path), "data": data},
    }


def _media_type(path: Path) -> str:
    media_type = _IMAGE_MEDIA_TYPES.get(path.suffix.lower())
    if media_type is None:
        raise AgentConfigError(f"unsupported image type for claude: {path}")
    return media_type


def _classify_result(
    result: ResultMessage, assistant_error: tuple[str, str] | None
) -> AgentError:
    detail = _result_detail(result)
    if assistant_error is not None:
        return _classify_code(assistant_error[0], assistant_error[1] or detail)
    return classify_failure(
        f"Claude error result ({result.subtype}): {detail}",
        login_hint=_LOGIN_HINT,
        status=result.api_error_status,
    )


def _classify_code(code: str, text: str) -> AgentError:
    message = f"Claude Code error ({code}): {text}"
    match code:
        case "authentication_failed":
            return AgentAuthError(f"{message} ({_LOGIN_HINT})")
        case "billing_error" | "rate_limit":
            return AgentQuotaError(message)
        case "invalid_request":
            return AgentConfigError(message)
        case _:
            return AgentTransientError(message)


def _result_detail(result: ResultMessage | None) -> str:
    if result is None:
        return ""
    return result.result or "; ".join(result.errors or []) or result.subtype
