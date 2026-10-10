"""Antigravity CLI (`agy`): the subscription Gemini backend, the only one that hears audio.

One turn is `agy … -p= --input-format stream-json --output-format
stream-json` with the user message as one stdin JSON line. Facts this
module is built on (agy 1.3.2):

* stdin messages carry text only, and agy ignores `@path` in them, so the
  runner lists media paths and asks the model to open each with `view_file`.
  Audio counts as heard only when a finished `view_file` step names its path;
  unheard audio becomes a `TurnDefect` that resends it.
* In stream-json input mode an error is reported and agy keeps waiting for
  the next message, so stdin closes as soon as the `result` record arrives.
* `--json-schema` output arrives through agy's `finish` tool and is copied
  to `result.structured_output`; a turn that never calls `finish` still
  reports the previous turn's value there, so it is trusted only after a
  `finish` step in the same turn. Without one, a JSON object in the turn's
  own final message (one ```json fence stripped) is the structured output.
* The effort is part of the model id (`gemini-3.1-pro-high`); `preflight`
  checks ids against `agy models`. A success is kept for good; a failure
  (e.g. a timeout) answers every task for 60 s, then the next one probes.
* MCP servers come from `<workdir>/.agents/mcp_config.json`; their calls
  appear as the `call_mcp_tool` tool.
* Paid API-key variables are removed from the environment so agy always
  uses the subscription login.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import TYPE_CHECKING, Any

from grillmaster.agents import process
from grillmaster.agents.adapters._jsonl import JsonlTurn, resolve_cli
from grillmaster.agents.adapters.base import (
    Capability,
    FinalOutput,
    MediaDelivery,
    TurnDefect,
    TurnRequest,
)
from grillmaster.agents.errors import (
    AgentConfigError,
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
    from collections.abc import Callable, Iterable, Iterator, Sequence
    from pathlib import Path

    from grillmaster.agents.events import AgentEvent
    from grillmaster.core.model_spec import ModelSpec

# agy offers low/medium/high suffixes; the higher repo efforts clamp to high.
_EFFORT_SUFFIX = {
    Effort.LOW: "low",
    Effort.MEDIUM: "medium",
    Effort.HIGH: "high",
    Effort.EXTRA: "high",
    Effort.MAX: "high",
    Effort.ULTRA: "high",
}
# Metered keys agy might prefer over the cached subscription login.
# ANTIGRAVITY_API_KEY stays: it is agy's own key.
API_KEY_ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY")
MCP_CONFIG_RELPATH = (".agents", "mcp_config.json")
_MODELS_TIMEOUT_S = 120.0
# How long a failed `agy models` probe answers for the tasks after it.
PROBE_FAILURE_TTL_S = 60.0
_LOGIN_HINT = "run `agy` once interactively to sign in"
_FINISH_TOOL = "finish"
_MCP_TOOL = "call_mcp_tool"
_VIEW_FILE_TOOL = "view_file"
# One whole-message ``` / ```json fence around the answer.
_JSON_FENCE_RE = re.compile(r"\A```(?:json)?[ \t]*\n(.*)\n```\Z", re.DOTALL)
_USAGE_NAMES = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_tokens": "cached_input_tokens",
    "thinking_tokens": "reasoning_tokens",
}


class AgyAdapter:
    backend = Backend.AGY
    capabilities = frozenset(
        {
            Capability.AUDIO_INPUT,
            Capability.IMAGE_INPUT,
            Capability.NATIVE_SCHEMA,
            Capability.RESUME,
            Capability.MCP,
            Capability.MCP_IMAGE_RESULT,
            Capability.WEB_SEARCH,
        }
    )
    media_delivery = MediaDelivery.VIEW_FILE

    def __init__(
        self,
        *,
        spawn: process.Spawn = process.spawn,
        executable: str | None = None,
        list_models: Callable[[], Sequence[str]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._spawn = spawn
        self._executable = executable
        self._list_models = list_models or self._models_from_cli
        self._clock = clock
        # The first successful probe, and the latest failed one with its time.
        self._models: frozenset[str] | None = None
        self._probe_failure: tuple[float, AgentConfigError] | None = None
        self._models_lock = threading.Lock()

    def preflight(
        self,
        spec: ModelSpec,
        images: Sequence[Path],  # noqa: ARG002 - the AgentAdapter signature
        audio: Sequence[Path],  # noqa: ARG002
    ) -> None:
        self.model_id(spec)

    def start(self, request: TurnRequest) -> JsonlTurn:
        return self._turn(request, conversation=None)

    def resume(self, session_id: str, request: TurnRequest) -> JsonlTurn:
        return self._turn(request, conversation=session_id)

    def model_id(self, spec: ModelSpec) -> str:
        """`model-effort`, checked against the ids `agy models` lists."""
        model_id = f"{spec.model}-{_EFFORT_SUFFIX[spec.effort]}"
        available = self._available_models()
        if model_id not in available:
            raise AgentConfigError(
                f"agy has no model {model_id!r} (from {spec}); "
                f"available: {', '.join(sorted(available))}"
            )
        return model_id

    def argv(self, request: TurnRequest, *, conversation: str | None) -> list[str]:
        argv = [
            resolve_cli("agy", self._executable),
            "--model",
            self.model_id(request.spec),
            "--dangerously-skip-permissions",
            "--log-file",
            str(request.session_dir / "agy.log"),
        ]
        if request.schema_path is not None:
            argv += ["--json-schema", str(request.schema_path)]
        for root in _readable_roots(request):
            argv += ["--add-dir", str(root)]
        if conversation is not None:
            argv += ["--conversation", conversation]
        # Every flag goes before `-p=`; a bare `-p` would swallow the next one.
        argv += ["--input-format", "stream-json", "--output-format", "stream-json"]
        argv.append("-p=")
        return argv

    def _turn(self, request: TurnRequest, *, conversation: str | None) -> JsonlTurn:
        argv = self.argv(request, conversation=conversation)
        _write_mcp_config(request)
        stdin = json.dumps(
            {"event": "user", "message": {"content": request.message}},
            ensure_ascii=False,
        )
        spec = process.ProcessSpec(
            argv=argv,
            cwd=request.workdir,
            timeout_s=request.timeout_s,
            env=scrubbed_env(),
            stdin=stdin + "\n",
            keep_stdin_open=True,
        )
        parser = AgyTurnParser(request.audio, schema=request.schema is not None)
        return JsonlTurn(self._spawn(spec), parser, request)

    def _available_models(self) -> frozenset[str]:
        with self._models_lock:
            if self._models is not None:
                return self._models
            if self._probe_failure is not None:
                failed_at, failure = self._probe_failure
                if self._clock() - failed_at < PROBE_FAILURE_TTL_S:
                    raise AgentConfigError(str(failure)) from failure
            try:
                self._models = frozenset(self._list_models())
            except AgentConfigError as error:
                self._probe_failure = (self._clock(), error)
                raise
            return self._models

    def _models_from_cli(self) -> list[str]:
        spec = process.ProcessSpec(
            argv=[resolve_cli("agy", self._executable), "models"],
            cwd=None,
            timeout_s=_MODELS_TIMEOUT_S,
            env=scrubbed_env(),
        )
        try:
            result = process.run_text(spec, spawn_fn=self._spawn)
        except process.TimeoutExpired as error:
            raise AgentConfigError(f"`agy models` timed out: {error}") from error
        if result.returncode != 0:
            raise AgentConfigError(
                f"`agy models` exited with code {result.returncode}: {result.stderr}"
            )
        return parse_model_list(result.stdout)


def parse_model_list(text: str) -> list[str]:
    """Model ids from `agy models` output (`<id>\\t<display name>` lines)."""
    return [
        line.split("\t", 1)[0].strip()
        for line in text.splitlines()
        if "\t" in line and line.split("\t", 1)[0].strip()
    ]


def scrubbed_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in API_KEY_ENV_VARS:
        env.pop(key, None)
    return env


class AgyTurnParser:
    """Reads one turn's `init` / `step_update` / `result` records; `schema`
    says the turn was asked for structured output."""

    def __init__(self, audio: Sequence[Path], *, schema: bool = False) -> None:
        self._audio = tuple(audio)
        self._schema = schema
        self._session_id = ""
        self._texts: dict[int, list[str]] = {}
        self._thoughts: dict[int, list[str]] = {}
        self._announced: set[int] = set()
        self._viewed: set[str] = set()
        self._finished = False
        # This turn's last finished agent message.
        self._last_text = ""
        self._result: dict[str, Any] | None = None

    @property
    def done(self) -> bool:
        return self._result is not None

    def feed(self, record: dict[str, Any]) -> Iterable[AgentEvent]:
        match record.get("event"):
            case "init":
                self._session_id = str(record.get("conversation_id") or "")
            case "step_update":
                yield from self._step(record.get("step_update") or {})
            case "result":
                self._result = record.get("result") or {}
            case _:
                pass

    def finish(self, returncode: int, stderr_tail: str) -> FinalOutput:
        result = self._result
        if result is None:
            raise AgentTransientError(
                f"agy exited with code {returncode} without a result: {stderr_tail}"
            )
        if result.get("status") != "SUCCESS":
            message = str(result.get("error") or f"agy turn failed: {result}")
            raise classify_failure(message, login_hint=_LOGIN_HINT)
        unheard = tuple(
            path for path in self._audio if _path_key(str(path)) not in self._viewed
        )
        return FinalOutput(
            session_id=str(result.get("conversation_id") or self._session_id),
            text=str(result.get("response") or ""),
            structured=self._structured(result),
            usage=normalize_usage(result.get("usage"), _USAGE_NAMES),
            defects=(unheard_audio_defect(unheard),) if unheard else (),
        )

    def _structured(self, result: dict[str, Any]) -> object | None:
        """The `finish` value of this turn, else a JSON object answered as
        this turn's final message; never an earlier turn's `finish` value."""
        if not self._schema:
            return None
        if self._finished:
            return result.get("structured_output")
        return json_object_answer(self._last_text)

    def _step(self, step: dict[str, Any]) -> Iterator[AgentEvent]:
        index = int(step.get("step_index", -1))
        done = step.get("state") == "DONE"
        match step.get("step_type"):
            case "agent_response":
                if delta := step.get("text_delta"):
                    self._texts.setdefault(index, []).append(str(delta))
                # Field present in agy's binary, not yet seen in a recording.
                if isinstance(thinking := step.get("thinking"), str) and thinking:
                    self._thoughts.setdefault(index, []).append(thinking)
                if done:
                    if thought := "".join(self._thoughts.pop(index, [])).strip():
                        yield Thought(thought)
                    if text := "".join(self._texts.pop(index, [])).strip():
                        self._last_text = text
                        yield Message(text)
            case "tool":
                yield from self._tool_step(index, step, done=done)
            case "finish":
                self._finished = True
            case _:
                pass

    def _tool_step(
        self, index: int, step: dict[str, Any], *, done: bool
    ) -> Iterator[AgentEvent]:
        info = step.get("tool_info") or {}
        name = str(info.get("name") or step.get("tool_name") or "")
        params: dict[str, Any] = info.get("parameters") or {}
        if name == _FINISH_TOOL:
            self._finished = True
            return
        if name == _MCP_TOOL:
            name = str(params.get("ToolName") or name)
            params = params.get("Arguments") or {}
        elif name == _VIEW_FILE_TOOL and done and "AbsolutePath" in params:
            self._viewed.add(_path_key(str(params["AbsolutePath"])))
        if index not in self._announced:
            self._announced.add(index)
            yield ToolCall(name, params)
        if done:
            yield ToolResult(name)


def json_object_answer(text: str) -> dict[str, Any] | None:
    """`text` (inside at most one ``` / ```json fence) as a JSON object, or
    `None`; whether it fits the schema is the output model's call."""
    stripped = text.strip()
    if fenced := _JSON_FENCE_RE.match(stripped):
        stripped = fenced.group(1)
    try:
        value = json.loads(stripped)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def unheard_audio_defect(paths: Sequence[Path]) -> TurnDefect:
    """A repair round that resends `paths` with the order to open them."""
    listed = "\n".join(f"- {path}" for path in paths)
    return TurnDefect(
        message=(
            "你沒有用 view_file 開啟下列音訊檔，所以沒有真正聽到它們：\n"
            f"{listed}\n請先逐一開啟聆聽，再依聽到的內容完成任務。"
        ),
        audio=tuple(paths),
    )


def _readable_roots(request: TurnRequest) -> list[Path]:
    roots = [
        request.workdir,
        *request.add_dirs,
        *(path.parent for path in (*request.images, *request.audio)),
    ]
    unique: dict[str, Path] = {}
    for root in roots:
        unique.setdefault(_path_key(str(root)), root)
    return list(unique.values())


def _write_mcp_config(request: TurnRequest) -> None:
    path = request.workdir.joinpath(*MCP_CONFIG_RELPATH)
    if request.mcp is None:
        # A config left by an earlier session with tools must not leak in.
        path.unlink(missing_ok=True)
        return
    config = {
        "mcpServers": {
            "grill": {"command": request.mcp.command, "args": list(request.mcp.args)}
        }
    }
    atomic_write_text(path, json.dumps(config, indent=2))


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))
